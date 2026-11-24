//! Alignment-based fitness and ETC-precision of the discovered models, measured with Rust4PM
//! (`process_mining` 0.7.1). Reproduces the quality table of the evaluation.
//!
//! Every Petri net `experiment-outputs/models/<log>/<model>.pnml` is measured against the original
//! event log `experiment-inputs/original-logs/<log file>`:
//!
//! * the log is read as variants of `concept:name`, in file order,
//! * every variant is aligned once with standard costs (log and model moves 1, synchronous and
//!   silent moves 0); the fitness is the alignment fitness averaged over the traces,
//! * the precision is the escaping-edges precision on these alignments with Rust4PM's ProM
//!   preset, and `f1` the harmonic mean of fitness and precision,
//! * both together get 10 minutes per model.
//!
//! The nets converted with ProM carry an empty `<finalmarkings/>`; their final marking is one
//! token in the net's sink, the only place without outgoing arcs.
//!
//! Run from this folder, after `experiment-inputs/assemble_logs.py`:
//!
//! ```text
//! cargo run --release
//! ```
//!
//! It writes one file per setting to `experiment-results/conformance/`, and the quality table of
//! the paper to `experiment-results/tables/quality.tex` and, for reading, `quality.md`.

use std::{
    collections::HashSet,
    fs::File,
    io::BufReader,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    time::Duration,
};

use process_mining::{
    conformance::{
        alignments::{
            compute_fitness, petri_net::AlignmentError, AlignmentOptions, VariantAlignmentResult,
        },
        precision::{EscapingEdgesOptions, PrecisionError},
    },
    core::{
        event_data::case_centric::{
            utils::activity_projection::EventLogActivityProjection,
            xes::{build_ignore_attributes, stream_xes, XESImportOptions},
        },
        process_models::case_centric::petri_net::{pnml::import_pnml, Marking, PlaceID},
    },
    utils::dijkstra_search::SearchError,
    AcceptingPetriNet, Control,
};
use serde::Serialize;
use serde_json::json;

/// Time limit per model, alignments and precision together
const TIMEOUT: Duration = Duration::from_secs(600);

/// The event logs: (model folder, original log file, name in the table)
const LOGS: &[(&str, &str, &str)] = &[
    ("Hospital_log", "Hospital_log.xes.gz", "BPIC 11: Hospital Log"),
    ("BPI_Challenge_2012", "BPI_Challenge_2012.xes.gz", "BPIC 12: Loan applications"),
    (
        "BPI_Challenge_2013_open_problems",
        "BPI_Challenge_2013_open_problems.xes.gz",
        "BPIC 13: Open Problems",
    ),
    (
        "BPI_Challenge_2013_closed_problems",
        "BPI_Challenge_2013_closed_problems.xes.gz",
        "BPIC 13: Closed Problems",
    ),
    (
        "BPI_Challenge_2013_incidents",
        "BPI_Challenge_2013_incidents.xes.gz",
        "BPIC 13: Incidents",
    ),
    ("BPIC17_Offer_log", "BPIC17 - Offer log.xes.gz", "BPIC 17: Offer log"),
    ("BPI_Challenge_2018", "BPI_Challenge_2018.xes.gz", "BPIC 18: Payment process"),
    (
        "BPI_Challenge_2019",
        "BPI_Challenge_2019.xes.gz",
        "BPIC 19: Purchase order handling",
    ),
    ("sepsis", "Sepsis Cases.xes.gz", "Sepsis Cases"),
    (
        "road_traffic_fine_management",
        "road_traffic_fine_management.xes.gz",
        "Road Traffic Fines",
    ),
];

/// A combination of algorithm, parameters and data, one column group of the table
struct Setting {
    /// The model in every log folder, `<model>.pnml`
    model: &'static str,
    /// Its results file in `experiment-results/conformance`
    file: &'static str,
    algorithm: &'static str,
    input: &'static str,
    /// (name, value) of the discovery parameters
    parameters: &'static [(&'static str, f64)],
    /// Column name in `quality.md`
    title: &'static str,
}

/// The settings, in the column order of the table
const SETTINGS: &[Setting] = &[
    Setting {
        model: "SplitMiner1.0",
        file: "split-miner-1.0.json",
        algorithm: "Split Miner 1.0",
        input: "original log",
        parameters: &[("epsilon", 0.1), ("eta", 0.4)],
        title: "Split Miner 1.0",
    },
    Setting {
        model: "FederatedSplitMiner_original",
        file: "federated-split-miner_original.json",
        algorithm: "Federated Split Miner",
        input: "original log",
        parameters: &[("epsilon", 0.1), ("eta", 0.4)],
        title: "FSM (original log)",
    },
    Setting {
        model: "FederatedSplitMiner_federated",
        file: "federated-split-miner_federated.json",
        algorithm: "Federated Split Miner",
        input: "three partial logs",
        parameters: &[("epsilon", 0.1), ("eta", 0.4)],
        title: "FSM (partial logs)",
    },
    Setting {
        model: "dfg_im_00",
        file: "inductive-miner-df_tau0.0.json",
        algorithm: "Inductive Miner directly-follows",
        input: "original log",
        parameters: &[("tau", 0.0)],
        title: "IMd tau=0",
    },
    Setting {
        model: "dfg_im_01",
        file: "inductive-miner-df_tau0.1.json",
        algorithm: "Inductive Miner directly-follows",
        input: "original log",
        parameters: &[("tau", 0.1)],
        title: "IMd tau=0.1",
    },
    Setting {
        model: "dfg_im_02",
        file: "inductive-miner-df_tau0.2.json",
        algorithm: "Inductive Miner directly-follows",
        input: "original log",
        parameters: &[("tau", 0.2)],
        title: "IMd tau=0.2",
    },
];

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Measured,
    /// The time limit was exceeded
    Timeout,
    /// The log or the net could not be read, or no measure could be taken (see `error`)
    Failed,
    /// There is no Petri net for this model
    Missing,
}

#[derive(Debug, Serialize)]
struct Measurement {
    log: &'static str,
    log_file: &'static str,
    fitness: Option<f64>,
    precision: Option<f64>,
    f1: Option<f64>,
    status: Status,
    error: Option<String>,
}

/// Stream a log into its variants by `concept:name`, in file order
fn read_log(path: &Path) -> Result<EventLogActivityProjection, String> {
    let file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let options = XESImportOptions {
        ignore_log_attributes_except: Some(HashSet::new()),
        ignore_trace_attributes_except: Some(build_ignore_attributes(["concept:name"])),
        ignore_event_attributes_except: Some(build_ignore_attributes(["concept:name"])),
        ..Default::default()
    };
    let (mut stream, _) =
        stream_xes(BufReader::new(file), "xes.gz", options).map_err(|e| format!("{e:?}"))?;
    let projection = EventLogActivityProjection::from(&mut stream);
    match stream.error {
        Some(e) => Err(format!("{}: {e:?}", path.display())),
        None => Ok(projection),
    }
}

/// Read a Petri net; without a final marking, it is one token in the net's unique sink
fn read_net(path: &Path) -> Result<AcceptingPetriNet, String> {
    let file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let (net, initial, final_marking) =
        import_pnml(BufReader::new(file)).map_err(|e| format!("{e:?}"))?;
    let initial = initial
        .filter(|m| !m.is_empty())
        .ok_or("the net has no initial marking")?;
    let final_marking = match final_marking {
        Some(m) => m,
        None => {
            let sinks: Vec<PlaceID> = net
                .places()
                .map(|(p, _)| p)
                .filter(|&p| net.postset_of_place(p).len() == 0)
                .collect();
            let [sink] = sinks[..] else {
                return Err(format!(
                    "the net has no final marking and {} sinks",
                    sinks.len()
                ));
            };
            let mut m = Marking::new();
            m.add(sink, 1);
            m
        }
    };
    Ok(AcceptingPetriNet::new(net, initial, final_marking))
}

/// Fitness and precision of `net`; `Err(None)` if the time limit was exceeded
fn measure(
    net: &AcceptingPetriNet,
    log: &EventLogActivityProjection,
    cancel: &AtomicBool,
) -> Result<(f64, f64), Option<String>> {
    let ctl = Control::new().cancel_flag(cancel);
    let align = AlignmentOptions::default();

    let alignments: Vec<VariantAlignmentResult> =
        ctl.align_variants(net, log, &align).map_err(|_| None)?;
    if let Some(e) = alignments.iter().find_map(|v| v.result.as_ref().err()) {
        return Err(Some(match e {
            AlignmentError::SearchError(SearchError::Unreachable) => {
                "the final marking is unreachable".to_string()
            }
            e => format!("alignment: {e}"),
        }));
    }
    let fitness = compute_fitness(net, &alignments, &align)
        .map_err(|e| Some(format!("fitness: {e}")))?
        .average_fitness;
    let precision = ctl
        .escaping_edges_precision_from_alignments(net, &alignments, &EscapingEdgesOptions::prom())
        .map_err(|e| match e {
            PrecisionError::Cancelled => None,
            e => Some(format!("precision: {e}")),
        })?
        .precision;
    Ok((fitness, precision))
}

/// [`measure`] under [`TIMEOUT`]
fn measure_with_timeout(
    net: &AcceptingPetriNet,
    log: &EventLogActivityProjection,
) -> Result<(f64, f64), Option<String>> {
    let cancel = AtomicBool::new(false);
    let (done, wait) = mpsc::channel::<()>();
    std::thread::scope(|s| {
        let cancel = &cancel;
        s.spawn(move || {
            if let Err(mpsc::RecvTimeoutError::Timeout) = wait.recv_timeout(TIMEOUT) {
                cancel.store(true, Ordering::Relaxed);
            }
        });
        let result = measure(net, log, cancel);
        drop(done);
        result
    })
}

/// Measure every model of every log; `results[s][l]` is setting `s` on log `l`
fn measure_all(repo: &Path) -> Vec<Vec<Measurement>> {
    let mut results: Vec<Vec<Measurement>> = SETTINGS.iter().map(|_| Vec::new()).collect();
    for &(folder, log_file, log_name) in LOGS {
        eprintln!("{log_name}");
        let log = read_log(&repo.join("experiment-inputs/original-logs").join(log_file));
        for (setting, column) in SETTINGS.iter().zip(results.iter_mut()) {
            let net_file = format!("experiment-outputs/models/{folder}/{}.pnml", setting.model);
            let path = repo.join(&net_file);
            let mut m = Measurement {
                log: log_name,
                log_file,
                fitness: None,
                precision: None,
                f1: None,
                status: Status::Failed,
                error: None,
            };
            let outcome = match (&log, path.is_file()) {
                (Err(e), _) => Err(Some(format!("log: {e}"))),
                (Ok(_), false) => {
                    m.status = Status::Missing;
                    Err(Some(format!("no Petri net {net_file}")))
                }
                (Ok(log), true) => read_net(&path)
                    .map_err(|e| Some(format!("net: {e}")))
                    .and_then(|net| measure_with_timeout(&net, log)),
            };
            match outcome {
                Ok((f, p)) => {
                    m.status = Status::Measured;
                    m.fitness = Some(f);
                    m.precision = Some(p);
                    m.f1 = Some(if f + p == 0.0 { 0.0 } else { 2.0 * f * p / (f + p) });
                }
                Err(None) => m.status = Status::Timeout,
                Err(Some(e)) => m.error = Some(e),
            }
            eprintln!(
                "  {}: {:?} {}",
                setting.model,
                m.status,
                m.error.as_deref().unwrap_or_default()
            );
            column.push(m);
        }
    }
    results
}

fn write(path: &Path, content: String) {
    std::fs::write(path, content).unwrap_or_else(|e| panic!("writing {}: {e}", path.display()));
}

/// One file per setting: its algorithm, input, parameters and model, and one entry per log
fn write_conformance(dir: &Path, results: &[Vec<Measurement>]) {
    for (setting, column) in SETTINGS.iter().zip(results) {
        let parameters: serde_json::Map<String, serde_json::Value> = setting
            .parameters
            .iter()
            .map(|&(name, value)| (name.to_string(), json!(value)))
            .collect();
        let file = json!({
            "algorithm": setting.algorithm,
            "input": setting.input,
            "parameters": parameters,
            "model_file": format!("{}.pnml", setting.model),
            "results": column,
        });
        let text = serde_json::to_string_pretty(&file).expect("serializing the results") + "\n";
        write(&dir.join(setting.file), text);
    }
}

/// A value as printed in the tables, `None` if it was not measured
fn printed(x: Option<f64>) -> Option<String> {
    x.map(|v| format!("{v:.3}"))
}

/// The highest F1 of every log as printed; all settings printing it are the best of the row
fn best_f1(results: &[Vec<Measurement>], log: usize) -> Option<String> {
    results
        .iter()
        .filter_map(|column| column[log].f1)
        .max_by(f64::total_cmp)
        .and_then(|v| printed(Some(v)))
}

fn quality_md(results: &[Vec<Measurement>]) -> String {
    let cell = |x: Option<f64>| printed(x).unwrap_or_else(|| "--".to_string());
    let mut md = String::from(
        "### Alignment-based fitness, ETC-precision and their harmonic mean\n\n\
         Measured with Rust4PM (`process_mining` 0.7.1) on the original event logs: the \
         alignment fitness averaged over the traces, and the escaping-edges precision on the \
         alignments with the ProM preset. A dash marks a value that was not measured; the status \
         column of each setting says why.\n\n\
         #### Harmonic mean per setting\n\n| Event log |",
    );
    for setting in SETTINGS {
        md += &format!(" {} |", setting.title);
    }
    md += &format!("\n|---|{}\n", "--:|".repeat(SETTINGS.len()));
    for (l, (_, _, log_name)) in LOGS.iter().enumerate() {
        md += &format!("| {log_name} |");
        let best = best_f1(results, l);
        for column in results {
            let value = cell(column[l].f1);
            if best.as_deref() == Some(value.as_str()) {
                md += &format!(" **{value}** |");
            } else {
                md += &format!(" {value} |");
            }
        }
        md += "\n";
    }
    for (setting, column) in SETTINGS.iter().zip(results) {
        let parameters: Vec<String> = setting
            .parameters
            .iter()
            .map(|&(name, value)| format!("{name} = {value:?}"))
            .collect();
        md += &format!(
            "\n#### {}\n\n{}, {}, {}.\n\n\
             | Event log | fitness | precision | F1 | status |\n|---|--:|--:|--:|---|\n",
            setting.title,
            setting.algorithm,
            setting.input,
            parameters.join(", ")
        );
        for m in column {
            let status = serde_json::to_value(m.status).expect("a status");
            md += &format!(
                "| {} | {} | {} | {} | {} |\n",
                m.log,
                cell(m.fitness),
                cell(m.precision),
                cell(m.f1),
                status.as_str().expect("a string")
            );
        }
    }
    md
}

/// The LaTeX the paper includes; the highest F1 of every log is set in bold
fn quality_tex(results: &[Vec<Measurement>]) -> String {
    let mut tex = String::from(QUALITY_TEX_HEAD);
    for (l, (_, _, log_name)) in LOGS.iter().enumerate() {
        let best = best_f1(results, l);
        let mut cells = vec![log_name.to_string()];
        for column in results {
            let m = &column[l];
            let f1 = printed(m.f1);
            cells.push(printed(m.fitness).unwrap_or_else(|| "---".to_string()));
            cells.push(printed(m.precision).unwrap_or_else(|| "---".to_string()));
            cells.push(match f1 {
                Some(v) if best.as_deref() == Some(v.as_str()) => format!("$\\mathbf{{{v}}}$"),
                Some(v) => v,
                None => "---".to_string(),
            });
        }
        tex += &format!("        {} \\\\\n", cells.join(" & "));
    }
    tex + QUALITY_TEX_TAIL
}

const QUALITY_TEX_HEAD: &str = r"\begin{table}[ht!]
    \caption{Alignment-based fitness (\emph{f}, averaged over the traces), ETC-precision (\emph{p}), and their harmonic mean (\emph{F\textsubscript{1}}) of the discovered models, measured with Rust4PM on the original event logs: standard alignment costs, and escaping-edges precision on the alignments with the ProM preset, mimicking the escaping-edges precision in ProM.
    \emph{SM 1.0} is the original Split Miner, \emph{FSM (original)} our
    federated Split Miner applied to the original event data, \emph{FSM (federated)} to the federated event data, and \emph{IMd} the Inductive Miner directly-follows at
    three noise thresholds. A dash marks a model whose final marking is unreachable, so that
    no trace can be aligned.}
    \label{split-miner:tab:quality}
    \centering
    \resizebox{\linewidth}{!}{\begin{tabular}{lrrrrrrrrrrrrrrrrrr}
        \toprule
        & \multicolumn{3}{c}{SM~1.0} & \multicolumn{3}{c}{FSM (original)}
        & \multicolumn{3}{c}{FSM (federated)} & \multicolumn{3}{c}{IMd $\tau=0$}
        & \multicolumn{3}{c}{IMd $\tau=0.1$} & \multicolumn{3}{c}{IMd $\tau=0.2$} \\
        \cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}\cmidrule(lr){11-13}\cmidrule(lr){14-16}\cmidrule(lr){17-19}
        Event log & \emph{f} & \emph{p} & \emph{F\textsubscript{1}}
        & \emph{f} & \emph{p} & \emph{F\textsubscript{1}}
        & \emph{f} & \emph{p} & \emph{F\textsubscript{1}}
        & \emph{f} & \emph{p} & \emph{F\textsubscript{1}}
        & \emph{f} & \emph{p} & \emph{F\textsubscript{1}}
        & \emph{f} & \emph{p} & \emph{F\textsubscript{1}} \\
        \midrule
";

const QUALITY_TEX_TAIL: &str = r"        \bottomrule
    \end{tabular}}
\end{table}
";

fn main() {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let results = measure_all(&repo);
    write_conformance(&repo.join("experiment-results/conformance"), &results);
    let tables = repo.join("experiment-results/tables");
    write(&tables.join("quality.md"), quality_md(&results));
    write(&tables.join("quality.tex"), quality_tex(&results));
}
