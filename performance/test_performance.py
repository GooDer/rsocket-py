import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from multiprocessing import Process

import pytest

from performance.performance_client import PerformanceClient
from performance.performance_server import run_server


@pytest.mark.timeout(5)
@pytest.mark.performance
async def test_request_response(unused_tcp_port):
    async with run_against_server(unused_tcp_port) as client:
        await record_runtime('request_response', client.request_response)


@pytest.mark.timeout(300)
@pytest.mark.performance
async def test_request_stream(unused_tcp_port):
    async with run_against_server(unused_tcp_port) as client:
        arguments = dict(response_count=1,
                         response_size=1_000_000)
        await record_runtime(f'request_stream {arguments}',
                             lambda: client.request_stream(**arguments), iterations=1)


def run_server_async(unused_tcp_port):
    asyncio.run(run_server(unused_tcp_port))


@asynccontextmanager
async def run_against_server(unused_tcp_port: int) -> PerformanceClient:
    server_process = Process(target=run_server_async, args=[unused_tcp_port])
    server_process.start()
    await asyncio.sleep(1)  # todo: replace with wait for server

    try:
        async with run_with_client(unused_tcp_port) as client:
            yield client
    finally:
        server_process.kill()
        pass


@asynccontextmanager
async def run_with_client(unused_tcp_port):
    async with PerformanceClient(unused_tcp_port) as client:
        yield client


async def record_runtime(request_type, coroutine_generator, iterations=1000, output_filename='results.csv'):
    run_times = []

    for i in range(iterations):
        start_time = datetime.now()
        await coroutine_generator()
        run_times.append(datetime.now() - start_time)

    average_runtime = sum(run_times, timedelta(0)) / len(run_times)

    with open(output_filename, 'a') as fd:
        fd.write(f'{request_type}, {iterations}, {average_runtime.total_seconds()}\n')
