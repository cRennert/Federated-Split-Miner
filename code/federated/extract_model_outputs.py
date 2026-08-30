

from argparse import ArgumentParser
import json
import os
import shutil

from neonik.neon.computationreport import ComputationReportList, ComputationReport


def determine_commit_versions() -> dict[str, int]:
    import subprocess

    proc = subprocess.run(["git", "--no-pager", "log"], capture_output=True, text=True, check=True)
    commit_history = []
    for line in proc.stdout.split('\n'):
        if line.startswith("commit"):
            commit_history.append(line.split(' ')[1].strip())

    result = {}
    for i, commit in enumerate(reversed(commit_history)):
        result[commit] = i
    return result


def run():
    parser = ArgumentParser()
    parser.add_argument("--clean", "-c", dest="clean", action="store_true")
    args = parser.parse_args()

    out_folder = "extracted-model-outputs"
    if args.clean:
        shutil.rmtree(out_folder, ignore_errors=True)

    commit_versions = determine_commit_versions()

    for report in ComputationReportList.from_folder(os.path.join("evaluation-reports", "split-miner")):
        report: ComputationReport

        if report.custom_metadata is None or "model-output" not in report.custom_metadata:
            continue
        if report.experiment_spec is None:
            continue
        if report.custom_version is None or commit_versions[report.custom_version] < commit_versions['9d8c40cb64a62ed54f2b05195b1186e5e12b5dc8']:
            continue

        event_log = report.experiment_spec['component-properties']['event-log']
        report_id = os.path.split(report.filename)[1][:-len(".json")]

        model_out_folder = os.path.join(out_folder, event_log, report_id)
        if not os.path.isdir(model_out_folder):
            os.makedirs(model_out_folder)

            for (filename, content) in report.custom_metadata["model-output"].items():
                if content is None:
                    continue
                with open(os.path.join(model_out_folder, filename), "w") as f:
                    f.write(content)

            with open(os.path.join(model_out_folder, f"{report_id}.json"), 'w') as f:
                json.dump(report.to_serializable_dict(), f)


if __name__ == "__main__":
    run()
