import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    logging.info("🎉 Hello from child bot.py! Your script is running 24/7.")
    count = 0
    while True:
        count += 1
        logging.info(f"⚡ bot.py heart beat #{count} - everything is running smoothly!")
        time.sleep(10)

if __name__ == "__main__":
    main()
