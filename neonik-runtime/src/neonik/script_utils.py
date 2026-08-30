import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import aiofiles
import aiofiles.os
import aiohttp
import yaml
from tqdm.asyncio import tqdm_asyncio

from .spec import ExperimentSpec


class NeonikCredentials:
    __backend: str
    __project_id: str
    __token: str

    def __init__(self, backend: str, project_id: str, token: str):
        self.__backend = backend
        self.__project_id = project_id
        self.__token = token

    @staticmethod
    async def from_config(filename: str = ".neonik-creds.yml") -> "NeonikCredentials":
        async with aiofiles.open(filename, 'r') as f:
            d = yaml.safe_load(await f.read())
            project = d['project']
            return NeonikCredentials(project['server'].rstrip("/"), project['id'], project['token'])

    @property
    def backend(self) -> str:
        return self.__backend

    @property
    def project_id(self) -> str:
        return self.__project_id

    @property
    def token(self) -> str:
        return self.__token


async def upload_experiments(creds: NeonikCredentials, specs: list[ExperimentSpec],
                             update_computations: bool, update_failed_experiments_only: bool) -> None:
    if update_failed_experiments_only:
        update_computations = False

    current_to_upload: list[tuple[ExperimentSpec, bool, bool]] = [
        (spec, True, update_computations) for spec in specs
    ]
    next_to_upload: list[tuple[ExperimentSpec, bool, bool]] = []

    async def upload_spec(session: aiohttp.ClientSession, spec: ExperimentSpec, first_upload: bool = True, force_update: bool = False) -> None:
        payload = {
            "experiment_specification": spec.serialize(),
            "update_computations": update_computations or force_update
        }
        retry = False
        try:
            async with session.post(f"{creds.backend}/project/{creds.project_id}/experiments/", json=payload,
                                    headers={"Authorization": creds.token}) as response:
                assert response.status == 200
                if update_failed_experiments_only:
                    response_body = await response.json()
                    if response_body['experiment']['state'] == 'failed':
                        payload['update_computations'] = True
                        retry = True
                        print(response_body['experiment']['identifier'])

            if retry and first_upload:
                next_to_upload.append((spec, False, True))
                # await upload_spec(session, spec, False, True)
        except asyncio.TimeoutError:
            next_to_upload.append((spec, first_upload, force_update))
        except aiohttp.ServerDisconnectedError:
            pass  # Both are a keyboard interrupt

    current_pass = 1
    while len(current_to_upload) > 0 and current_pass < 10:
        next_to_upload.clear()
        async with aiohttp.ClientSession() as session:
            coroutines = [upload_spec(session, spec, first_upload, force_upload)
                          for (spec, first_upload, force_upload) in current_to_upload]
            await tqdm_asyncio.gather(*coroutines, desc=f"Pushing experiments (Pass {current_pass})")
        current_to_upload = list(next_to_upload)
        current_pass += 1


@dataclass
class ComputationReportId:
    identifier: str
    experiment_id: str


async def fetch_report_ids(creds: NeonikCredentials) -> list[ComputationReportId]:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{creds.backend}/project/{creds.project_id}/reports/list",
                               headers={"Authorization": creds.token}) as response:
            assert response.status == 200
            content = await response.json()
            return [ComputationReportId(**report_id) for report_id in content]


async def download_experiment_specs(creds: NeonikCredentials, report_ids: list[ComputationReportId]) -> dict[str, dict[str, Any]]:
    result = {}
    async def download_spec(creds: NeonikCredentials, session: aiohttp.ClientSession, experiment_id: str) -> None:
        async with session.get(f"{creds.backend}/project/{creds.project_id}/experiments/{experiment_id}",
                               headers={"Authorization": creds.token}) as response:
            assert response.status == 200
            response_content = await response.json()
            result[experiment_id] = response_content['spec']

    experiment_ids = { report_id.experiment_id for report_id in report_ids }
    async with aiohttp.ClientSession() as session:
        downloads = [download_spec(creds, session, experiment_id) for experiment_id in experiment_ids]
        await tqdm_asyncio.gather(*downloads, desc="Downloading experiment specifications")
    return result


async def download_reports(creds: NeonikCredentials, report_ids: list[ComputationReportId], experiment_specs: dict[str, dict[str, Any]],
                           reports_folder: str, component_folders: bool) -> None:
    # Limit the number of concurrent requests to the server. This is needed as sending all requests at once
    # will lead to timeouts when we're downloading a lot of requests over a slow connection.
    semaphore = asyncio.Semaphore(100)

    async def download_report(creds: NeonikCredentials, session: aiohttp.ClientSession, report_id: ComputationReportId) -> None:
        async with semaphore:
            async with session.get(f"{creds.backend}/project/{creds.project_id}/experiments/{report_id.experiment_id}/reports/{report_id.identifier}",
                                   headers={"Authorization": creds.token}) as response:
                assert response.status == 200
                response_content = await response.json()
                computation_report = response_content['data']

                spec = experiment_specs[report_id.experiment_id]
                computation_report['experiment_spec'] = spec
                computation_report['custom_version'] = response_content['runtime_version']

                folder = reports_folder
                if component_folders:
                    folder = os.path.join(reports_folder, spec['component'])
                await aiofiles.os.makedirs(folder, exist_ok=True)

                async with aiofiles.open(os.path.join(folder, f"computation-{report_id.identifier}.json"), 'w') as f:
                    await f.write(json.dumps(response_content['data']))

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        downloads = [download_report(creds, session, report_id) for report_id in report_ids]
        await tqdm_asyncio.gather(*downloads, desc="Downloading reports")
