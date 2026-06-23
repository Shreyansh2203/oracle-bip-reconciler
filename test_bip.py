import asyncio
import os

import httpx

from src.services.oracle_bip import fetch_bip_receipts


async def main():
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            print("Querying with amount=1731.61...")
            res = await fetch_bip_receipts(
                client=client,
                username=os.getenv("ORACLE_USERNAME"),
                password=os.getenv("ORACLE_PASSWORD"),
                receipt_amount=1731.61,
                receipt_date="2026-03-02",
            )
            print(f"Success! {len(res)} results.")
        except Exception as e:
            print(f"Exception: {e}")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())
