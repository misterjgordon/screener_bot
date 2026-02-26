from smb_screener import run_once_mode
from smb_screener import run_polling_mode

# "once"  -> run the workflow a single time and exit
# "poll"  -> keep running every INTERVAL_SECONDS
# "off"   -> do nothing (handy when you temporarily disable the script)
RUN_MODE = 'poll'  # poll or once
# *******************************************
# Interval between API calls when in polling mode (in seconds) SMB updates every 10 seconds.
INTERVAL_SECONDS = 10  # 20 seconds


def main():
    if RUN_MODE == 'once':
        run_once_mode()
    elif RUN_MODE == 'poll':
        run_polling_mode(INTERVAL_SECONDS)
    else:
        print("RUN_MODE is 'off', exiting.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped by user.')
