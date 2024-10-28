# solana-snapshot-finder
Automatic search and download of snapshots for Solana  

Also check online public RPC finder - https://solana.rpc-finder.com/

## Navigation  

* [Description](#what-exactly-does-the-script-do)
* [Getting Started]()
    - [Using docker](#run-via-docker)  
    - [Without docker](#without-docker)  
* [How to update](#update)

## What exactly does the script do:  
1. Finds all available RPCs  
2. Get the number of the current slot  
3. In multi-threaded mode, checks the slot numbers of all snapshots on all RPCs  
*Starting from version 0.1.3, only the first 10 RPCs speed are tested in a loop. [See details here](https://github.com/c29r3/solana-snapshot-finder/releases/tag/0.1.3)
5. List of RPCs sorted by lowest latency
`slots_diff = current_slot - snapshot_slot`
5. Checks the download speed from RPC with the most recent snapshot. If `download_speed <min_download_speed`, then it checks the speed at the next node.  
6. Download snapshot  
```bash
options:
  -h, --help            show this help message and exit
  -t THREADS_COUNT, --threads-count THREADS_COUNT
                        the number of concurrently running threads that check snapshots for rpc nodes
  -r RPC_ADDRESS, --rpc_address RPC_ADDRESS
                        RPC address of the node from which the current slot number will be taken
                        https://api.mainnet-beta.solana.com
  --slot SLOT           search for a snapshot with a specific slot number (useful for network restarts)
  --version VERSION     search for a snapshot from a specific version node
  --wildcard_version WILDCARD_VERSION
                        search for a snapshot with a major / minor version e.g. 1.18 (excluding .23)
  --max_snapshot_age MAX_SNAPSHOT_AGE
                        How many slots ago the snapshot was created (in slots)
  --min_download_speed MIN_DOWNLOAD_SPEED
                        Minimum average snapshot download speed in megabytes
  --max_download_speed MAX_DOWNLOAD_SPEED
                        Maximum snapshot download speed in megabytes - https://github.com/c29r3/solana-
                        snapshot-finder/issues/11. Example: --max_download_speed 192
  --max_latency MAX_LATENCY
                        The maximum value of latency (milliseconds). If latency > max_latency --> skip
  --with_private_rpc    Enable adding and checking RPCs with the --private-rpc option.This slow down
                        checking and searching but potentially increases the number of RPCs from which
                        snapshots can be downloaded.
  --measurement_time MEASUREMENT_TIME
                        Time in seconds during which the script will measure the download speed
  --snapshot_path SNAPSHOT_PATH
                        The location where the snapshot will be downloaded (absolute path). Example:
                        /home/ubuntu/solana/validator-ledger
  --num_of_retries NUM_OF_RETRIES
                        The number of retries if a suitable server for downloading the snapshot was not
                        found
  --sleep SLEEP         Sleep before next retry (seconds)
  --sort_order SORT_ORDER
                        Priority way to sort the found servers. latency or slots_diff
  -ipb IP_BLACKLIST, --ip_blacklist IP_BLACKLIST
                        Comma separated list of ip addresse (ip:port) that will be excluded from the scan.
                        Example: -ipb 1.1.1.1:8899,8.8.8.8:8899
  -b BLACKLIST, --blacklist BLACKLIST
                        If the same corrupted archive is constantly downloaded, you can exclude it. Specify
                        either the number of the slot you want to exclude, or the hash of the archive name.
                        You can specify several, separated by commas. Example: -b 135501350,135501360 or
                        --blacklist 135501350,some_hash
  -v, --verbose         increase output verbosity to DEBUG
```
![alt text](https://raw.githubusercontent.com/c29r3/solana-snapshot-finder/aec9a59a7517a5049fa702675bdc8c770acbef99/2021-07-23_22-38.png?raw=true)

### Without docker   
Install requirements  
```bash
sudo apt-get update \
&& sudo apt-get install python3-venv git -y \
&& git clone https://github.com/c29r3/solana-snapshot-finder.git \
&& cd solana-snapshot-finder \
&& python3 -m venv venv \
&& source ./venv/bin/activate \
&& pip3 install -r requirements.txt
```

Start script  
Mainnet  
```python
python3 snapshot-finder.py --snapshot_path $HOME/solana/validator-ledger
``` 
`$HOME/solana/validator-ledger/` - path to your `validator-ledger`

TdS  
```python
python3 snapshot-finder.py --snapshot_path $HOME/solana/validator-ledger -r http://api.testnet.solana.com
``` 

### Run via docker  
Mainnet  
```bash
sudo docker pull c29r3/solana-snapshot-finder:latest; \
sudo docker run -it --rm \
-v ~/solana/validator-ledger:/solana/snapshot \
--user $(id -u):$(id -g) \
c29r3/solana-snapshot-finder:latest \
--snapshot_path /solana/snapshot
```
*`~/solana/validator-ledger` - path to validator-ledger, where snapshots stored*

TdS  
```bash
sudo docker pull c29r3/solana-snapshot-finder:latest; \
sudo docker run -it --rm \
-v ~/solana/validator-ledger:/solana/snapshot \
--user $(id -u):$(id -g) \
c29r3/solana-snapshot-finder:latest \
--snapshot_path /solana/snapshot \
-r http://api.testnet.solana.com
```

## Update  
`sudo docker pull c29r3/solana-snapshot-finder:latest`
