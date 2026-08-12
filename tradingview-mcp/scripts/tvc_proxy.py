import argparse
import asyncio

LISTEN_HOST = '0.0.0.0'
TARGET_HOST = '127.0.0.1'


async def relay(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def handle(client_reader, client_writer, target_port):
    try:
        target_reader, target_writer = await asyncio.open_connection(TARGET_HOST, target_port)
    except Exception:
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        relay(client_reader, target_writer),
        relay(target_reader, client_writer),
    )


async def main(listen_port, target_port):
    server = await asyncio.start_server(
        lambda reader, writer: handle(reader, writer, target_port),
        LISTEN_HOST,
        listen_port,
    )
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='TCP bridge from WSL to Windows TradingView CDP'
    )
    parser.add_argument('--port', type=int, default=19222, help='listen port')
    parser.add_argument('--target-port', type=int, default=9222, help='Windows CDP port')
    args = parser.parse_args()
    asyncio.run(main(args.port, args.target_port))

