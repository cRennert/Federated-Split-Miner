#! /usr/local/bin/python3

import asyncio
import os.path
from argparse import ArgumentParser

import aiofiles
import aiofiles.os

from ..script_utils import NeonikCredentials, fetch_report_ids, ComputationReportId, download_experiment_specs, \
    download_reports


async def filter_already_existent_reports(report_ids: list[ComputationReportId], reports_folder: str, component_folder: bool) -> list[ComputationReportId]:
    async def find_existing_reports(folder: str, recursive: bool) -> set[str]:
        result = set()
        for name in await aiofiles.os.listdir(folder):
            full_path = os.path.join(folder, name)
            if recursive and await aiofiles.os.path.isdir(full_path):
                result.update(await find_existing_reports(full_path, recursive))
            elif name.startswith("computation-") and name.endswith(".json"):
                result.add(name[12:-5])
        return result

    already_fetched_ids = await find_existing_reports(reports_folder, component_folder)
    return [report_id for report_id in report_ids if report_id.identifier not in already_fetched_ids]


async def run():
    parser = ArgumentParser(prog="python3 -m neonik.scripts.pull-reports")
    parser.add_argument("--reports-folder", "-f", dest="reports_folder", type=str, default=None,
                        help="The folder to put the reports into, default is \"evaluation-reports\".")
    parser.add_argument("--component-folders", "-c", dest="component_folders", action="store_true",
                        help="Whether to create subfolders for individual components. WILL ALWAYS BE SET IF THE REPORTS FOLDER IS NOT SET MANUALLY. If the reports folder was given manually, you have to set this flag to have component subfolders.")
    args = parser.parse_args()
    reports_folder = args.reports_folder
    component_folders = args.component_folders
    if reports_folder is None:
        reports_folder = "evaluation-reports"
        component_folders = True

    creds = await NeonikCredentials.from_config()

    await aiofiles.os.makedirs(reports_folder, exist_ok=True)

    report_ids = await fetch_report_ids(creds)
    report_ids = await filter_already_existent_reports(report_ids, reports_folder, component_folders)
    experiment_specs = await download_experiment_specs(creds, report_ids)
    await download_reports(creds, report_ids, experiment_specs, reports_folder, component_folders)


def main():
    # For UV scripts
    asyncio.run(run())


if __name__ == "__main__":
    main()