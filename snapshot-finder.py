import os
import glob
import requests
import time
import shutil
import math
import json
import sys
import argparse
import logging
import subprocess
from pathlib import Path
from requests import ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError
from tqdm import tqdm
from multiprocessing.dummy import Pool as ThreadPool
import statistics

parser = argparse.ArgumentParser(description='Solana snapshot finder')
parser.add_argument('-t', '--threads-count', default=1000, type=int,
    help='the number of concurrently running threads that check snapshots for rpc nodes')
parser.add_argument('-r', '--rpc_address',
    default='https://api.mainnet-beta.solana.com', type=str,
    help='RPC address of the node from which the current slot number will be taken\n'
         'https://api.mainnet-beta.solana.com')

parser.add_argument("--slot", default=0, type=int,
                     help="search for a snapshot with a specific slot number (useful for network restarts)")
parser.add_argument("--version", default=None, help="search for a snapshot from a specific version node")
parser.add_argument("--wildcard_version", default=None, help="search for a snapshot with a major / minor version e.g. 1.18 (excluding .23)")
parser.add_argument('--max_snapshot_age', default=1300, type=int, help='How many slots ago the snapshot was created (in slots)')
parser.add_argument('--min_download_speed', default=60, type=int, help='Minimum average snapshot download speed in megabytes')
parser.add_argument('--max_download_speed', type=int,
help='Maximum snapshot download speed in megabytes - https://github.com/c29r3/solana-snapshot-finder/issues/11. Example: --max_download_speed 192')
parser.add_argument('--max_latency', default=100, type=int, help='The maximum value of latency (milliseconds). If latency > max_latency --> skip')
parser.add_argument('--with_private_rpc', action="store_true", help='Enable adding and checking RPCs with the --private-rpc option.This slow down checking and searching but potentially increases'
                    ' the number of RPCs from which snapshots can be downloaded.')
parser.add_argument('--measurement_time', default=7, type=int, help='Time in seconds during which the script will measure the download speed')
parser.add_argument('--snapshot_path', type=str, default=".", help='The location where the snapshot will be downloaded (absolute path).'
                                                                     ' Example: /home/ubuntu/solana/validator-ledger')
parser.add_argument('--num_of_retries', default=5, type=int, help='The number of retries if a suitable server for downloading the snapshot was not found')
parser.add_argument('--sleep', default=7, type=int, help='Sleep before next retry (seconds)')
parser.add_argument('--sort_order', default='latency', type=str, help='Priority way to sort the found servers. latency or slots_diff')
parser.add_argument('-ipb', '--ip_blacklist', default='', type=str, help='Comma separated list of ip addresse (ip:port) that will be excluded from the scan. Example: -ipb 1.1.1.1:8899,8.8.8.8:8899')
parser.add_argument('-b', '--blacklist', default='', type=str, help='If the same corrupted archive is constantly downloaded, you can exclude it.'
                    ' Specify either the number of the slot you want to exclude, or the hash of the archive name. '
                    'You can specify several, separated by commas. Example: -b 135501350,135501360 or --blacklist 135501350,some_hash')
parser.add_argument("-v", "--verbose", help="increase output verbosity to DEBUG", action="store_true")
args = parser.parse_args()

DEFAULT_HEADERS = {"Content-Type": "application/json"}
RPC = args.rpc_address
SPECIFIC_SLOT = int(args.slot)
SPECIFIC_VERSION = args.version
WILDCARD_VERSION = args.wildcard_version
MAX_SNAPSHOT_AGE_IN_SLOTS = args.max_snapshot_age
WITH_PRIVATE_RPC = args.with_private_rpc
THREADS_COUNT = args.threads_count
MIN_DOWNLOAD_SPEED_MB = args.min_download_speed
MAX_DOWNLOAD_SPEED_MB = args.max_download_speed
SPEED_MEASURE_TIME_SEC = args.measurement_time
MAX_LATENCY = args.max_latency
SNAPSHOT_PATH = args.snapshot_path if args.snapshot_path[-1] != '/' else args.snapshot_path[:-1]
NUM_OF_MAX_ATTEMPTS = args.num_of_retries
SLEEP_BEFORE_RETRY = args.sleep
NUM_OF_ATTEMPTS = 1
SORT_ORDER = args.sort_order
BLACKLIST = str(args.blacklist).split(",")
IP_BLACKLIST = str(args.ip_blacklist).split(",")
FULL_LOCAL_SNAP_SLOT = 0

current_slot = 0
DISCARDED_BY_ARCHIVE_TYPE = 0
DISCARDED_BY_LATENCY = 0
DISCARDED_BY_SLOT = 0
DISCARDED_BY_VERSION = 0
DISCARDED_BY_UNKNW_ERR = 0
DISCARDED_BY_TIMEOUT = 0
FULL_LOCAL_SNAPSHOTS = []
# skip servers that do not fit the filters so as not to check them again
unsuitable_servers = set()
# Configure Logging
logging.getLogger('urllib3').setLevel(logging.WARNING)
if args.verbose:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(f'{SNAPSHOT_PATH}/snapshot-finder.log'),
            logging.StreamHandler(sys.stdout),
        ]
    )

else:
    # logging.basicConfig(stream=sys.stdout, encoding='utf-8', level=logging.INFO, format='|%(asctime)s| %(message)s')
        logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(f'{SNAPSHOT_PATH}/snapshot-finder.log'),
            logging.StreamHandler(sys.stdout),
        ]
    )
logger = logging.getLogger(__name__)


def convert_size(size_bytes):
   if size_bytes == 0:
    return "0B"
   size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return "%s %s" % (s, size_name[i])


def measure_speed(url: str, measure_time: int) -> float:
    logging.debug('measure_speed()')
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
    global DISCARDED_BY_UNKNW_ERR
    global DISCARDED_BY_TIMEOUT
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

    except (ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError) as reqErr:
        # logger.debug(f'error in do_request(): {reqErr=}')
        DISCARDED_BY_TIMEOUT += 1
        return f'error in do_request(): {reqErr}'

    except Exception as unknwErr:
        DISCARDED_BY_UNKNW_ERR += 1
        # logger.debug(f'error in do_request(): {unknwErr=}')
        return f'error in do_request(): {reqErr}'


def get_current_slot():
    logger.debug("get_current_slot()")
    d = '{"jsonrpc":"2.0","id":1, "method":"getSlot"}'
    try:
        r = do_request(url_=RPC, method_='post', data_=d, timeout_=25)
        if 'result' in str(r.text):
            return r.json()["result"]
        else:
            logger.error(f'Can\'t get current slot')
            logger.debug(r.status_code)
            return None

    except (ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError) as connectErr:
        logger.debug(f'Can\'t get current slot\n{connectErr}')
    except Exception as unknwErr:
        logger.error(f'Can\'t get current slot\n{unknwErr}')
        return None


def get_all_rpc_ips():
    global DISCARDED_BY_VERSION

    logger.debug("get_all_rpc_ips()")
    d = '{"jsonrpc":"2.0", "id":1, "method":"getClusterNodes"}'
    r = do_request(url_=RPC, method_='post', data_=d, timeout_=25)
    if 'result' in str(r.text):
        rpc_ips = []
        for node in r.json()["result"]:
            if (WILDCARD_VERSION is not None and node["version"] and WILDCARD_VERSION not in node["version"]) or \
               (SPECIFIC_VERSION is not None and node["version"] and node["version"] != SPECIFIC_VERSION):
                DISCARDED_BY_VERSION += 1
                continue
            if node["rpc"] is not None:
                rpc_ips.append(node["rpc"])
            elif WITH_PRIVATE_RPC is True:
                gossip_ip = node["gossip"].split(":")[0]
                rpc_ips.append(f'{gossip_ip}:8899')

        rpc_ips = list(set(rpc_ips))
        logger.debug(f'RPC_IPS LEN before blacklisting {len(rpc_ips)}')
        # removing blacklisted ip addresses
        if IP_BLACKLIST is not None:
            rpc_ips = list(set(rpc_ips) - set(IP_BLACKLIST))
        logger.debug(f'RPC_IPS LEN after blacklisting {len(rpc_ips)}')
        return rpc_ips

    else:
        logger.error(f'Can\'t get RPC ip addresses {r.text}')
        sys.exit()


def get_snapshot_slot(rpc_address: str):
    global FULL_LOCAL_SNAP_SLOT
    global DISCARDED_BY_ARCHIVE_TYPE
    global DISCARDED_BY_LATENCY
    global DISCARDED_BY_SLOT

    pbar.update(1)
    url = f'http://{rpc_address}/snapshot.tar.bz2'
    inc_url = f'http://{rpc_address}/incremental-snapshot.tar.bz2'
    # d = '{"jsonrpc":"2.0","id":1,"method":"getHighestSnapshotSlot"}'
    try:
        r = do_request(url_=inc_url, method_='head', timeout_=1)
        if 'location' in str(r.headers) and 'error' not in str(r.text) and r.elapsed.total_seconds() * 1000 > MAX_LATENCY:
            DISCARDED_BY_LATENCY += 1
            return None

        if 'location' in str(r.headers) and 'error' not in str(r.text):
            snap_location_ = r.headers["location"]
            if snap_location_.endswith('tar') is True:
                DISCARDED_BY_ARCHIVE_TYPE += 1
                return None
            incremental_snap_slot = int(snap_location_.split("-")[2])
            snap_slot_ = int(snap_location_.split("-")[3])
            slots_diff = current_slot - snap_slot_

            if slots_diff < -100:
                logger.error(f'Something wrong with this snapshot\\rpc_node - {slots_diff=}. This node will be skipped {rpc_address=}')
                DISCARDED_BY_SLOT += 1
                return

            if slots_diff > MAX_SNAPSHOT_AGE_IN_SLOTS:
                DISCARDED_BY_SLOT += 1
                return

            if FULL_LOCAL_SNAP_SLOT == incremental_snap_slot:
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [snap_location_]
                })
                return

            r2 = do_request(url_=url, method_='head', timeout_=1)
            if 'location' in str(r.headers) and 'error' not in str(r.text):
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [r.headers["location"], r2.headers['location']],
                })
                return

        r = do_request(url_=url, method_='head', timeout_=1)
        if 'location' in str(r.headers) and 'error' not in str(r.text):
            snap_location_ = r.headers["location"]
            # filtering uncompressed archives
            if snap_location_.endswith('tar') is True:
                DISCARDED_BY_ARCHIVE_TYPE += 1
                return None
            full_snap_slot_ = int(snap_location_.split("-")[1])
            slots_diff_full = current_slot - full_snap_slot_
            if slots_diff_full <= MAX_SNAPSHOT_AGE_IN_SLOTS and r.elapsed.total_seconds() * 1000 <= MAX_LATENCY:
                # print(f'{rpc_address=} | {slots_diff=}')
                json_data["rpc_nodes"].append({
                    "snapshot_address": rpc_address,
                    "slots_diff": slots_diff_full,
                    "latency": r.elapsed.total_seconds() * 1000,
                    "files_to_download": [snap_location_]
                })
                return
        return None

    except Exception as getSnapErr_:
        return None


def download(url: str):
    fname = url[url.rfind('/'):].replace("/", "")
    temp_fname = f'{SNAPSHOT_PATH}/tmp-{fname}'
    # try:
    #     resp = requests.get(url, stream=True)
    #     total = int(resp.headers.get('content-length', 0))
    #     with open(temp_fname, 'wb') as file, tqdm(
    #         desc=fname,
    #         total=total,
    #         unit='iB',
    #         unit_scale=True,
    #         unit_divisor=1024,
    #     ) as bar:
    #         for data in resp.iter_content(chunk_size=1024):
    #             size = file.write(data)
    #             bar.update(size)

    #     logger.info(f'Rename the downloaded file {temp_fname} --> {fname}')
    #     os.rename(temp_fname, f'{SNAPSHOT_PATH}/{fname}')

    # except (ReadTimeout, ConnectTimeout, HTTPError, Timeout, ConnectionError) as downlErr:
    #     logger.error(f'Exception in download() func\n {downlErr}')

    try:
        # dirty trick with wget. Details here - https://github.com/c29r3/solana-snapshot-finder/issues/11
        if MAX_DOWNLOAD_SPEED_MB is not None:
            process = subprocess.run([wget_path, '--progress=dot:giga', f'--limit-rate={MAX_DOWNLOAD_SPEED_MB}M',
                                      '--trust-server-names', url, f'-O{temp_fname}'],
              stdout=subprocess.PIPE,
              universal_newlines=True)
        else:
            process = subprocess.run([wget_path, '--progress=dot:giga', '--trust-server-names', url, f'-O{temp_fname}'],
              stdout=subprocess.PIPE,
              universal_newlines=True)

        logger.info(f'Rename the downloaded file {temp_fname} --> {fname}')
        os.rename(temp_fname, f'{SNAPSHOT_PATH}/{fname}')

    except Exception as unknwErr:
        logger.error(f'Exception in download() func. Make sure wget is installed\n{unknwErr}')


def main_worker():
    try:
        global FULL_LOCAL_SNAP_SLOT
        rpc_nodes = list(set(get_all_rpc_ips()))
        global pbar
        pbar = tqdm(total=len(rpc_nodes))
        logger.info(f'RPC servers in total: {len(rpc_nodes)} | Current slot number: {current_slot}\n')

        # Search for full local snapshots.
        # If such a snapshot is found and it is not too old, then the script will try to find and download an incremental snapshot
        FULL_LOCAL_SNAPSHOTS = glob.glob(f'{SNAPSHOT_PATH}/snapshot-*tar*')
        if len(FULL_LOCAL_SNAPSHOTS) > 0:
            FULL_LOCAL_SNAPSHOTS.sort(reverse=True)
            FULL_LOCAL_SNAP_SLOT = FULL_LOCAL_SNAPSHOTS[0].replace(SNAPSHOT_PATH, "").split("-")[1]
            logger.info(f'Found full local snapshot {FULL_LOCAL_SNAPSHOTS[0]} | {FULL_LOCAL_SNAP_SLOT=}')

        else:
            logger.info(f'Can\'t find any full local snapshots in this path {SNAPSHOT_PATH} --> the search will be carried out on full snapshots')

        print(f'Searching information about snapshots on all found RPCs')
        pool = ThreadPool()
        pool.map(get_snapshot_slot, rpc_nodes)
        logger.info(f'Found suitable RPCs: {len(json_data["rpc_nodes"])}')
        logger.info(f'The following information shows for what reason and how many RPCs were skipped.'
        f'Timeout most probably mean, that node RPC port does not respond (port is closed)\n'
        f'{DISCARDED_BY_ARCHIVE_TYPE=} | {DISCARDED_BY_LATENCY=} |'
        f' {DISCARDED_BY_SLOT=} | {DISCARDED_BY_VERSION=} | {DISCARDED_BY_TIMEOUT=} | {DISCARDED_BY_UNKNW_ERR=}')

        if len(json_data["rpc_nodes"]) == 0:
            logger.info(f'No snapshot nodes were found matching the given parameters: {args.max_snapshot_age=}')
            sys.exit()

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
        logger.info(f'All data is saved to json file - {SNAPSHOT_PATH}/snapshot.json')

        best_snapshot_node = {}
        num_of_rpc_to_check = 15

        rpc_nodes_inc_sorted = []
        logger.info("TRYING TO DOWNLOADING FILES")
        for i, rpc_node in enumerate(json_data["rpc_nodes"], start=1):
            # filter blacklisted snapshots
            if BLACKLIST != ['']:
                if any(i in str(rpc_node["files_to_download"]) for i in BLACKLIST):
                    logger.info(f'{i}\\{len(json_data["rpc_nodes"])} BLACKLISTED --> {rpc_node}')
                    continue

            logger.info(f'{i}\\{len(json_data["rpc_nodes"])} checking the speed {rpc_node}')
            if rpc_node["snapshot_address"] in unsuitable_servers:
                logger.info(f'Rpc node already in unsuitable list --> skip {rpc_node["snapshot_address"]}')
                continue

            down_speed_bytes = measure_speed(url=rpc_node["snapshot_address"], measure_time=SPEED_MEASURE_TIME_SEC)
            down_speed_mb = convert_size(down_speed_bytes)
            if down_speed_bytes < MIN_DOWNLOAD_SPEED_MB * 1e6:
                logger.info(f'Too slow: {rpc_node=} {down_speed_mb=}')
                unsuitable_servers.add(rpc_node["snapshot_address"])
                continue

            elif down_speed_bytes >= MIN_DOWNLOAD_SPEED_MB * 1e6:
                logger.info(f'Suitable snapshot server found: {rpc_node=} {down_speed_mb=}')
                for path in reversed(rpc_node["files_to_download"]):
                    # do not download full snapshot if it already exists locally
                    if str(path).startswith("/snapshot-"):
                        full_snap_slot__ = path.split("-")[1]
                        if full_snap_slot__ == FULL_LOCAL_SNAP_SLOT:
                            continue


                    if 'incremental' in path:
                        r = do_request(f'http://{rpc_node["snapshot_address"]}/incremental-snapshot.tar.bz2', method_='head', timeout_=2)
                        if 'location' in str(r.headers) and 'error' not in str(r.text):
                            best_snapshot_node = f'http://{rpc_node["snapshot_address"]}{r.headers["location"]}'
                        else:
                            best_snapshot_node = f'http://{rpc_node["snapshot_address"]}{path}'

                    else:
                        best_snapshot_node = f'http://{rpc_node["snapshot_address"]}{path}'
                    logger.info(f'Downloading {best_snapshot_node} snapshot to {SNAPSHOT_PATH}')
                    download(url=best_snapshot_node)
                return 0

            elif i > num_of_rpc_to_check:
                logger.info(f'The limit on the number of RPC nodes from'
                ' which we measure the speed has been reached {num_of_rpc_to_check=}\n')
                break

            else:
                logger.info(f'{down_speed_mb=} < {MIN_DOWNLOAD_SPEED_MB=}')

        if best_snapshot_node is {}:
            logger.error(f'No snapshot nodes were found matching the given parameters:{args.min_download_speed=}'
                  f'\nTry restarting the script with --with_private_rpc'
                  f'RETRY #{NUM_OF_ATTEMPTS}\\{NUM_OF_MAX_ATTEMPTS}')
            return 1



    except KeyboardInterrupt:
        sys.exit('\nKeyboardInterrupt - ctrl + c')

    except:
        return 1


logger.info("Version: 0.3.9")
logger.info("https://github.com/c29r3/solana-snapshot-finder\n\n")
logger.info(f'{RPC=}\n'
      f'{MAX_SNAPSHOT_AGE_IN_SLOTS=}\n'
      f'{MIN_DOWNLOAD_SPEED_MB=}\n'
      f'{MAX_DOWNLOAD_SPEED_MB=}\n'
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
    logger.error(f'\nCheck {SNAPSHOT_PATH=} and permissions')
    Path(SNAPSHOT_PATH).mkdir(parents=True, exist_ok=True)

wget_path = shutil.which("wget")

if wget_path is None:
    logger.error("The wget utility was not found in the system, it is required")
    sys.exit()

json_data = ({"last_update_at": 0.0,
              "last_update_slot": 0,
              "total_rpc_nodes": 0,
              "rpc_nodes_with_actual_snapshot": 0,
              "rpc_nodes": []
              })


while NUM_OF_ATTEMPTS <= NUM_OF_MAX_ATTEMPTS:
    if SPECIFIC_SLOT != 0 and type(SPECIFIC_SLOT) is int:
        current_slot = SPECIFIC_SLOT
        MAX_SNAPSHOT_AGE_IN_SLOTS = 0
    else:
        current_slot = get_current_slot()
    logger.info(f'Attempt number: {NUM_OF_ATTEMPTS}. Total attempts: {NUM_OF_MAX_ATTEMPTS}')
    NUM_OF_ATTEMPTS += 1

    if current_slot is None:
        continue

    worker_result = main_worker()

    if worker_result == 0:
        logger.info("Done")
        exit(0)

    if worker_result != 0:
        logger.info("Now trying with flag --with_private_rpc")
        WITH_PRIVATE_RPC = True

    if NUM_OF_ATTEMPTS >= NUM_OF_MAX_ATTEMPTS:
        logger.error(f'Could not find a suitable snapshot --> exit')
        sys.exit()

    logger.info(f"Sleeping {SLEEP_BEFORE_RETRY} seconds before next try")
    time.sleep(SLEEP_BEFORE_RETRY)
