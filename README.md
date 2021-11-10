# solana-snapshot-finder
Automatic search and download of snapshots for Solana  

## Navigation  

* [Description](#what-exactly-does-the-script-do)
* [Getting Started]()
    - [Using docker](#run-via-docker) *champions choice 🙂
    - [Without docker](#without-docker) *You can, but you better not 🙂
* [How to update](#Update)

## What exactly does the script do:  
1. Finds all available RPCs  
2. Get the number of the current slot  
3. In multi-threaded mode, checks the slot numbers of all snapshots on all RPCs  
*Starting from version 0.1.3, only the first 10 RPCs speed are tested in a loop. [See details here](https://github.com/c29r3/solana-snapshot-finder/releases/tag/0.1.3)
5. Sorts from newest snapshots to older  
`slots_diff = current_slot - snapshot_slot`
5. Checks the download speed from RPC with the most recent snapshot. If `download_speed <min_download_speed`, then it checks the speed at the next node.  
6. Download snapshot  
```bash
Solana snapshot finder

optional arguments:
  -h, --help            show this help message and exit
  -t THREADS_COUNT, --threads-count THREADS_COUNT
                        the number of concurrently running threads that check snapshots for rpc nodes
  -r RPC_ADDRESS, --rpc_address RPC_ADDRESS
                        RPC address of the node from which the current slot number will be taken https://api.mainnet-beta.solana.com
  --max_snapshot_age MAX_SNAPSHOT_AGE
                        How many slots ago the snapshot was created (in slots)
  --min_download_speed MIN_DOWNLOAD_SPEED
                        Minimum average snapshot download speed in megabytes
  --measurement_time MEASUREMENT_TIME
                        Time in seconds during which the script will measure the download speed
  --snapshot_path SNAPSHOT_PATH
                        The location where the snapshot will be downloaded (absolute path). Example: /home/ubuntu/solana/validator-ledger
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
sudo docker run -it --rm \
-v ~/solana/validator-ledger:/solana/snapshot \
--user $(id -u):$(id -g) \
c29r3/solana-snapshot-finder:latest \
--snapshot_path /solana/snapshot
```
*`~/solana/validator-ledger` - path to validator-ledger, where snapshots stored*

TdS  
```bash
sudo docker run -it --rm \
-v ~/solana/validator-ledger:/solana/snapshot \
--user $(id -u):$(id -g) \
c29r3/solana-snapshot-finder:latest \
--snapshot_path /solana/snapshot \
-r http://api.testnet.solana.com
```

## Update  
`docker pull c29r3/solana-snapshot-finder:latest`
