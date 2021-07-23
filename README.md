# solana-snapshot-finder
Automatic search and download of snapshots for Solana  

## What exactly does the script do:  
1. Finds all available RPCs  
2. Get the number of the current slot  
3. In multi-threaded mode, checks the slot numbers of all snapshots on all RPCs  
4. Sorts from newest snapshots to older  
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

## Run via docker  
```
cd ~; \
mkdir snapshot; \
chmod 777 snapshot; \
docker run -it   --rm   \
-v $(pwd)/snapshot:/solana/snapshot   \
c29r3/solana-snapshot-finder:latest   \
--snapshot_path /solana/snapshot
```

