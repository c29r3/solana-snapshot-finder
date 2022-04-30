import os
import glob
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

print("Version: 0.2.2")
print("https://github.com/c29r3/solana-snapshot-finder\n\n")

parser = argparse.ArgumentParser(description='Solana snapshot finder')
parser.add_argument('-t', '--threads-count', default=1000, type=int,
    help='the number of concurrently running threads that check snapshots for rpc nodes')

parser.add_argument('-r', '--rpc_address',
    default='https://api.mainnet-beta.solana.com', type=str,
    help='RPC address of the node from which the current slot number will be taken\n'
         'https://api.mainnet-beta.solana.com')

parser.add_argument('--max_snapshot_age', default=900, type=int, help='How many slots ago the snapshot was created (in slots)')
parser.add_argument('--min_download_speed', default=25, type=int, help='Minimum average snapshot download speed in megabytes')
parser.add_argument('--max_latency', default=70, type=int, help='The maximum value of latency (milliseconds). If latency > max_latency --> skip')
parser.add_argument('--with_private_rpc', action="store_true", help='Enable adding and checking RPCs with the --private-rpc option.This slow down checking and searching but potentially increases the number of RPCs from which snapshots can be downloaded.')
parser.add_argument('--measurement_time', default=7, type=int, help='Time in seconds during which the script will measure the download speed')
parser.add_argument('--snapshot_path', type=str, default=".", help='The location where the snapshot will be downloaded (absolute path).'
                                                                     ' Example: /home/ubuntu/solana/validator-ledger')
parser.add_argument('--num_of_retries', default=5, type=int, help='The number of retries if a suitable server for downloading the snapshot was not found')
parser.add_argument('--sleep', default=20, type=int, help='Sleep before next retry (seconds)')
parser.add_argument('--sort_order', default='latency', type=str, help='Priority way to sort the found servers. latency or slots_diff')
args = parser.parse_args()

DEFAULT_HEADERS = {"Content-Type": "application/json"}
RPC = args.rpc_address
WITH_PRIVATE_RPC = args.with_private_rpc
MAX_SNAPSHOT_AGE_IN_SLOTS = args.max_snapshot_age
THREADS_COUNT = args.threads_count
MIN_DOWNLOAD_SPEED_MB = args.min_download_speed
SPEED_MEASURE_TIME_SEC = args.measurement_time
MAX_LATENCY = args.max_latency
SNAPSHOT_PATH = args.snapshot_path if args.snapshot_path[-1] != '\\' else args.snapshot_path[:-1]
NUM_OF_MAX_ATTEMPTS = args.num_of_retries
SLEEP_BEFORE_RETRY = args.sleep
NUM_OF_ATTEMPTS = 1
SORT_ORDER = args.sort_order
AVERAGE_SNAPSHOT_FILE_SIZE_MB = 2500.0
AVERAGE_INCREMENT_FILE_SIZE_MB = 200.0
AVERAGE_CATCHUP_SPEED = 2.0
FULL_LOCAL_SNAP_SLOT = 0

current_slot = 0
FULL_LOCAL_SNAPSHOTS = []
# skip servers that do not fit the filters so as not to check them again
unsuitable_servers = set()

print(f'{RPC=}\n'
      f'{MAX_SNAPSHOT_AGE_IN_SLOTS=}\n'
      f'{MIN_DOWNLOAD_SPEED_MB=}\n'
      f'{SNAPSHOT_PATH=}\n'
      f'{THREADS_COUNT=}\n'
      f'{NUM_OF_MAX_ATTEMPTS=}\n'
      f'{WITH_PRIVATE_RPC=}\n'
      f'{SORT_ORDER=}')

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
    url = f'http://{url}/snapshot.tar.bz2'
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
    try:
        r = do_request(url_=RPC, method_='post', data_=d, timeout_=25)
        if 'result' in str(r.text):
            return r.json()["result"]
        else:
            print(f'Can\'t get current slot')
            exit(1)
    except:
        print(f'Can\'t get current slot')
        exit(1)


def get_all_rpc_ips():
    d = '{"jsonrpc":"2.0", "id":1, "method":"getClusterNodes"}'
    r = do_request(url_=RPC, method_='post', data_=d, timeout_=25)
    if 'result' in str(r.text):
        if WITH_PRIVATE_RPC is True:
            rpc_ips = []
            for node in r.json()["result"]:
                if node["rpc"] is not None:
                    rpc_ips.append(node["rpc"])
                else:
                    gossip_ip = node["gossip"].split(":")[0]
                    rpc_ips.append(f'{gossip_ip}:8899')

        else:
            rpc_ips = [rpc["rpc"] for rpc in r.json()["result"] if rpc["rpc"] is not None]

        rpc_ips = list(set(rpc_ips))
        return rpc_ips

    else:
        sys.exit(f'Can\'t get RPC ip addresses {r.text}')


def get_snapshot_slot(rpc_address: str):
    global FULL_LOCAL_SNAP_SLOT
    pbar.update(1)
    url = f'http://{rpc_address}/snapshot.tar.bz2'
    inc_url = f'http://{rpc_address}/incremental-snapshot.tar.bz2'
    # d = '{"jsonrpc":"2.0","id":1,"method":"getHighestSnapshotSlot"}'
    try:
        r = do_request(url_=inc_url, method_='head', timeout_=1)
        if 'location' in str(r.headers) and 'error' not in str(r.text) and r.elapsed.total_seconds() * 1000 > MAX_LATENCY:
            return None

        if 'location' in str(r.headers) and 'error' not in str(r.text):
            snap_location_ = r.headers["location"]
            if snap_location_.endswith('tar') is True:
                return None
            incremental_snap_slot = int(snap_location_.split("-")[2])
            snap_slot_ = int(snap_location_.split("-")[3])
            slots_diff = current_slot - snap_slot_

            if slots_diff > MAX_SNAPSHOT_AGE_IN_SLOTS:
                return

            if FULL_LOCAL_SNAP_SLOT == incremental_snap_slot:
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [snap_location_],
                    "cost": AVERAGE_INCREMENT_FILE_SIZE_MB / MIN_DOWNLOAD_SPEED_MB + slots_diff / AVERAGE_CATCHUP_SPEED
                })
                return

            r2 = do_request(url_=url, method_='head', timeout_=1)
            if 'location' in str(r.headers) and 'error' not in str(r.text):
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [r.headers["location"], r2.headers['location']],
                    "cost": (AVERAGE_SNAPSHOT_FILE_SIZE_MB + AVERAGE_INCREMENT_FILE_SIZE_MB) / MIN_DOWNLOAD_SPEED_MB + slots_diff / AVERAGE_CATCHUP_SPEED
                })
                return

        r = do_request(url_=url, method_='head', timeout_=1)
        if 'location' in str(r.headers) and 'error' not in str(r.text):
            snap_location_ = r.headers["location"]
            # filtering uncompressed archives
            if snap_location_.endswith('tar') is True:
                return None
            full_snap_slot_ = int(snap_location_.split("-")[1])
            slots_diff_full = current_slot - full_snap_slot_
            if slots_diff_full <= MAX_SNAPSHOT_AGE_IN_SLOTS and r.elapsed.total_seconds() * 1000 <= MAX_LATENCY:
                # print(f'{rpc_address=} | {slots_diff=}')
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff_full,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [snap_location_],
                    "cost": AVERAGE_SNAPSHOT_FILE_SIZE_MB / MIN_DOWNLOAD_SPEED_MB + slots_diff_full / AVERAGE_CATCHUP_SPEED
                })
                return
        return None

    except Exception as getSnapErr:
        # print(f'error in get_snapshot_slot(): {getSnapErr}')
        return None


def download(url: str):
    actual_name = url[url.rfind('/'):]
    fname = f'{SNAPSHOT_PATH}{actual_name}'
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
        global FULL_LOCAL_SNAP_SLOT
        rpc_nodes = list(set(get_all_rpc_ips()))
        global pbar
        pbar = tqdm(total=len(rpc_nodes))
        print(f'RPC servers in total: {len(rpc_nodes)} \nCurrent slot number: {current_slot}\n')

        # Search for full local snapshots.
        # If such a snapshot is found and it is not too old, then the script will try to find and download an incremental snapshot
        FULL_LOCAL_SNAPSHOTS = glob.glob(f'{SNAPSHOT_PATH}/snapshot-*tar*')
        if len(FULL_LOCAL_SNAPSHOTS) > 0:
            FULL_LOCAL_SNAPSHOTS.sort(reverse=False)
            FULL_LOCAL_SNAP_SLOT = FULL_LOCAL_SNAPSHOTS[0].replace(SNAPSHOT_PATH, "").split("-")[1]
            print(f'Found full local snapshot {FULL_LOCAL_SNAPSHOTS[0]} | {FULL_LOCAL_SNAP_SLOT=}')

        else:
            print(f'Can\'t find any full local snapshots in this path {SNAPSHOT_PATH} --> the search will be carried out on full snapshots')

        print(f'Searching information about snapshots on all found RPCs')
        pool = ThreadPool()
        pool.map(get_snapshot_slot, rpc_nodes)
        print(f'Found suitable RPCs: {len(json_data["rpc_nodes"])}')

        if len(json_data["rpc_nodes"]) == 0:
            sys.exit(f'No snapshot nodes were found matching the given parameters:\n'
                     f'- {args.max_snapshot_age=}')

        # sort list of rpc node by SORT_ORDER (latency)
        rpc_nodes_sorted = sorted(json_data["rpc_nodes"], key=lambda k: k[SORT_ORDER])

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
        num_of_rpc_to_check = 15

        rpc_nodes_inc_sorted = []
        print("TRYING TO DOWNLOADING FILES")
        for i, rpc_node in enumerate(json_data["rpc_nodes"], start=1):
            print(f'{i}\\{len(json_data["rpc_nodes"])} checking the speed {rpc_node}')
            if rpc_node["snapshot_address"] in unsuitable_servers:
                print(f'Rpc node already in unsuitable list --> skip {rpc_node["snapshot_address"]}')
                continue

            down_speed_bytes = measure_speed(url=rpc_node["snapshot_address"], measure_time=SPEED_MEASURE_TIME_SEC)
            down_speed_mb = convert_size(down_speed_bytes)
            if down_speed_bytes < MIN_DOWNLOAD_SPEED_MB * 1e6:
                print(f'Too slow: {rpc_node=} {down_speed_mb=}')
                unsuitable_servers.add(rpc_node["snapshot_address"])
                continue

            elif down_speed_bytes >= MIN_DOWNLOAD_SPEED_MB * 1e6:
                print(f'Suitable snapshot server found: {rpc_node=} {down_speed_mb=}')
                for path in rpc_node["files_to_download"]:
                    best_snapshot_node = f'http://{rpc_node["snapshot_address"]}{path}'
                    print(f'Downloading {best_snapshot_node} snapshot to {SNAPSHOT_PATH}')
                    download(url=best_snapshot_node)
                return 0

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



    except KeyboardInterrupt:
        sys.exit('\nKeyboardInterrupt - ctrl + c')

    except:
        return 1


while NUM_OF_ATTEMPTS <= NUM_OF_MAX_ATTEMPTS:
    current_slot = get_current_slot()
    print(f'Attempt number: {NUM_OF_ATTEMPTS}. Total attempts: {NUM_OF_MAX_ATTEMPTS}')
    NUM_OF_ATTEMPTS += 1
    worker_result = main_worker()

    if worker_result == 0:
        print("Done")
        exit(0)

    if NUM_OF_ATTEMPTS >= NUM_OF_MAX_ATTEMPTS:
        sys.exit(f'Could not find a suitable snapshot')
    
    print(f"Sleeping {SLEEP_BEFORE_RETRY} seconds before next try")
    time.sleep(SLEEP_BEFORE_RETRY)
