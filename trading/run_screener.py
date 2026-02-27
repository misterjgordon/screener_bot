from trading.config import INTERVAL_SECONDS, RUN_MODE
from smb_screener import run_once_mode, run_polling_mode


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
