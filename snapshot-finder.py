import os
import requests
import time
import math
import json
import sys
import argparse
from requests import RequestException, Timeout
from tqdm import tqdm
from multiprocessing.dummy import Pool as ThreadPool
import statistics
import signal

print("Version: 0.1.3")

parser = argparse.ArgumentParser(description='Solana snapshot finder')
parser.add_argument('-t', '--threads-count', default=100, type=int,
    help='the number of concurrently running threads that check snapshots for rpc nodes')

parser.add_argument('-r', '--rpc_address',
    default='https://api.mainnet-beta.solana.com', type=str,
    help='RPC address of the node from which the current slot number will be taken\n'
         'https://api.mainnet-beta.solana.com')

parser.add_argument('--max_snapshot_age', default=600, type=int, help='How many slots ago the snapshot was created (in slots)')
parser.add_argument('--min_download_speed', default=25, type=int, help='Minimum average snapshot download speed in megabytes')
parser.add_argument('--measurement_time', default=15, type=int, help='Time in seconds during which the script will measure the download speed')
parser.add_argument('--snapshot_path', type=str, default=".", help='The location where the snapshot will be downloaded (absolute path).'
                                                                     ' Example: /home/ubuntu/solana/validator-ledger')
args = parser.parse_args()
print(args.rpc_address)

DEFAULT_HEADERS = {"Content-Type": "application/json"}
RPC = args.rpc_address
MAX_SNAPSHOT_AGE_IN_SLOTS = args.max_snapshot_age
THREADS_COUNT = args.threads_count
MIN_DOWNLOAD_SPEED_MB = args.min_download_speed
SPEED_MEASURE_TIME_SEC = args.measurement_time
SNAPSHOT_PATH = args.snapshot_path
NUM_OF_ATTEMPTS = 0
NUM_OF_MAX_ATTEMPTS = 5
current_slot = 0

print(f'{RPC=}\n'
      f'{MAX_SNAPSHOT_AGE_IN_SLOTS=}\n'
      f'{MIN_DOWNLOAD_SPEED_MB=}\n'
      f'{SNAPSHOT_PATH=}\n'
      f'{THREADS_COUNT=}\n'
      f'{NUM_OF_MAX_ATTEMPTS=}')

try:
    f_ = open(f'{SNAPSHOT_PATH}/write_perm_test', 'w')
    f_.close()
    os.remove(f'{SNAPSHOT_PATH}/write_perm_test')
except IOError:
    print(f'\nCheck {SNAPSHOT_PATH=} and permissions')
    sys.exit(f'{os.system("ls -l")}')

json_data = ({"last_update_at": 0.0,
              "last_update_slot": 0,
              "total_rpc_nodes": 0,
              "rpc_nodes_with_actual_snapshot": 0,
              "rpc_nodes": []
              })


def convert_size(size_bytes):
   if size_bytes == 0:
       return "0B"
   size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return "%s %s" % (s, size_name[i])


def measure_speed(url: str, measure_time: int) -> float:
    r = requests.get(url, stream=True, timeout=measure_time+2)
    r.raise_for_status()
    start_time = time.monotonic_ns()
    last_time = start_time
    loaded = 0
    speeds = []
    for chunk in r.iter_content(chunk_size=81920):
        curtime = time.monotonic_ns()

        worktime = (curtime - start_time) / 1000000000
        if worktime >= measure_time:
            break

        delta = (curtime - last_time) / 1000000000
        loaded += len(chunk)
        if delta > 1:
            estimated_bytes_per_second = loaded * (1 / delta)
            # print(f'{len(chunk)}:{delta} : {estimated_bytes_per_second}')
            speeds.append(estimated_bytes_per_second)

            last_time = curtime
            loaded = 0

    return statistics.median(speeds)


def do_request(url_: str, method_: str = 'GET', data_: str = '', timeout_: int = 3,
               headers_: dict = None):
    r = ''
    if headers_ is None:
        headers_ = DEFAULT_HEADERS

    try:
        if method_.lower() == 'get':
            r = requests.get(url_, headers=headers_, timeout=(timeout_, timeout_))
        elif method_.lower() == 'post':
            r = requests.post(url_, headers=headers_, data=data_, timeout=(timeout_, timeout_))
        elif method_.lower() == 'head':
            r = requests.head(url_, headers=headers_, timeout=(timeout_, timeout_))
        # print(f'{r.content, r.status_code, r.text}')
        return r

    except (RequestException, Timeout, Exception) as reqErr:
        # print(f'error in do_request(): {reqErr}')
        return f'error in do_request(): {reqErr}'


def get_current_slot():
    print("get_current_slot()")
    d = '{"jsonrpc":"2.0","id":1, "method":"getSlot"}'
    r = do_request(url_=RPC, method_='post', data_=d)
    if 'result' in str(r.text):
        return r.json()["result"]
    else:
        print(f'Can\'t get current slot {r.text}')
        exit(1)


def get_all_rpc_ips():
    print("get_all_rpc_ips()")
    d = '{"jsonrpc":"2.0", "id":1, "method":"getClusterNodes"}'
    r = do_request(url_=RPC, method_='post', data_=d)
    if 'result' in str(r.text):
        rpc_ips = [rpc["rpc"] for rpc in r.json()["result"] if rpc["rpc"] is not None]
        return rpc_ips

    else:
        print(f'Can\'t get RPC ip addresses {r.text}')
        exit(1)


def get_snapshot_slot(rpc_address: str):
    url = f'http://{rpc_address}/snapshot.tar.bz2'
    try:
        r = do_request(url_=url, method_='head')
        if 'location' in str(r.headers) and 'error' not in str(r.text):
            snap_location = r.headers["location"]
            # filtering uncompressed archives
            if snap_location.endswith('tar') is True:
                return None
            snap_slot_ = int(snap_location.split("-")[1])
            slots_diff = current_slot - snap_slot_
            if slots_diff <= MAX_SNAPSHOT_AGE_IN_SLOTS:
                # print(f'{rpc_address=} | {slots_diff=}')
                json_data["rpc_nodes"].append({
                    "snapshot_address": url,
                    "slots_diff": slots_diff,
                    "snapshot_name": r.headers["location"]
                })
                return None

    except Exception as getSnapErr:
        # print(f'error in get_snapshot_slot(): {getSnapErr}')
        return None


def download(url: str, fname: str):
    resp = requests.get(url, stream=True)
    total = int(resp.headers.get('content-length', 0))
    with open(fname, 'wb') as file, tqdm(
        desc=fname,
        total=total,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in resp.iter_content(chunk_size=1024):
            size = file.write(data)
            bar.update(size)


def main_worker():
    try:
        print(f'{current_slot=}\n')

        rpc_nodes = list(set(get_all_rpc_ips()))
        print(f'{len(rpc_nodes)=}\n')

        print(f'getting all rpc snapshot slots')
        pool = ThreadPool()
        pool.map(get_snapshot_slot, rpc_nodes)

        if len(json_data["rpc_nodes"]) == 0:
            sys.exit(f'No snapshot nodes were found matching the given parameters:\n'
                     f'- {args.max_snapshot_age=}')

        # sort list of rpc node by slots_diff
        rpc_nodes_sorted = sorted(json_data["rpc_nodes"], key=lambda k: k['slots_diff'])
        # from pprint import pprint
        # pprint(json_data)

        json_data.update({
            "last_update_at": time.time(),
            "last_update_slot": current_slot,
            "total_rpc_nodes": len(rpc_nodes),
            "rpc_nodes_with_actual_snapshot": len(json_data["rpc_nodes"]),
            "rpc_nodes": rpc_nodes_sorted
        })

        with open(f'{SNAPSHOT_PATH}/snapshot.json', "w") as result_f:
            json.dump(json_data, result_f, indent=2)
        print(f'All data is saved to json file - {SNAPSHOT_PATH}/snapshot.json')

        best_snapshot_node = {}
        # If we assume that 1 slot = 400 ms, and the download speed check time is 15 seconds
        # Then for 1 check the snapshot lags behind by 37.5 slots, for 10 by 375 slots
        num_of_rpc_to_check = 10

        for i, rpc_node in enumerate(json_data["rpc_nodes"], start=1):
            print(f'{i}\\{len(json_data["rpc_nodes"])} checking the speed {rpc_node}')
            down_speed_bytes = measure_speed(url=rpc_node["snapshot_address"], measure_time=SPEED_MEASURE_TIME_SEC)
            down_speed_mb = convert_size(down_speed_bytes)
            if down_speed_bytes >= MIN_DOWNLOAD_SPEED_MB * 1e6:
                print(f'Suitable snapshot server found: {rpc_node=} {down_speed_mb=}')
                best_snapshot_node = rpc_node
                break
            
            elif i > num_of_rpc_to_check:
                print(f'The limit on the number of RPC nodes from'
                ' which we measure the speed has been reached {num_of_rpc_to_check=}\n')
                break

            else:
                print(f'{down_speed_mb=} < {MIN_DOWNLOAD_SPEED_MB=}')

        if best_snapshot_node is {}:
            print(f'No snapshot nodes were found matching the given parameters:{args.min_download_speed=}\n'
                  f'RETRY #{NUM_OF_ATTEMPTS}\\{NUM_OF_MAX_ATTEMPTS}')
            return 1

        print(f'Downloading snapshot to {SNAPSHOT_PATH}')
        if SNAPSHOT_PATH != "" or SNAPSHOT_PATH is not None:
            snap_name = f'{SNAPSHOT_PATH}{best_snapshot_node["snapshot_name"]}'
        else:
            snap_name = f'{best_snapshot_node["snapshot_name"]}'
        download(url=best_snapshot_node["snapshot_address"], fname=snap_name)
        return 0

    except:
        return 1


while NUM_OF_ATTEMPTS < 5:
    current_slot = get_current_slot()
    NUM_OF_ATTEMPTS += 1
    worker_result = main_worker()

    if worker_result == 0:
        print("Done")
        exit(0)

    if NUM_OF_ATTEMPTS >= NUM_OF_MAX_ATTEMPTS:
        sys.exit(f'Could not find a suitable snapshot')
    
    # signal.signal(signal.SIGINT, sys.exit('\nYou pressed Ctrl+C!'))
