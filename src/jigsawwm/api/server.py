from contextlib import contextmanager
from functools import partial
import threading
import asyncio
from fastapi import FastAPI
from uvicorn import Config, Server
from jigsawwm.app.daemon import Daemon
import logging
logging.basicConfig(level=logging.DEBUG)
_logger = logging.getLogger(__name__)

app = FastAPI()
daemon = None

@app.get("/next_theme")
async def next_theme():
    await asyncio.to_thread(daemon.wm.manager.next_theme)
    return {"operation": "next_theme", "status": "success"}


@app.get("/prev_theme")
async def prev_theme():
    await asyncio.to_thread(daemon.wm.manager.prev_theme)
    return {"operation": "prev_theme", "status": "success"}

@app.get("/next_window")
async def next_window():
    await asyncio.to_thread(daemon.wm.manager.next_window)
    return {"operation": "next_window", "status": "success"}

@app.get("/prev_window")
async def prev_window():
    await asyncio.to_thread(daemon.wm.manager.prev_window)
    return {"operation": "prev_window", "status": "success"}

@app.get("/set_master")
async def set_master():
    await asyncio.to_thread(daemon.wm.manager.set_master)
    return {"operation": "set_master", "status": "success"}

@app.get("/roll_next")
async def roll_next():
    await asyncio.to_thread(daemon.wm.manager.roll_next)
    return {"operation": "roll_next", "status": "success"}

@app.get("/roll_prev")
async def roll_prev():
    await asyncio.to_thread(daemon.wm.manager.roll_prev)
    return {"operation": "roll_prev", "status": "success"}

@app.get("/next_monitor")
async def next_monitor():
    await asyncio.to_thread(partial(daemon.wm.manager.switch_monitor, 1))
    return {"operation": "next_monitor", "status": "success"}

@app.get("/prev_monitor")
async def prev_monitor():
    await asyncio.to_thread(partial(daemon.wm.manager.switch_monitor, -1))
    return {"operation": "prev_monitor", "status": "success"}

@app.get("/move_to_next_monitor")
async def move_to_next_monitor():
    await asyncio.to_thread(daemon.wm.manager.move_to_next_monitor)
    return {"operation": "move_to_next_monitor", "status": "success"}

@app.get("/move_to_prev_monitor")
async def move_to_prev_monitor():
    await asyncio.to_thread(daemon.wm.manager.move_to_prev_monitor)
    return {"operation": "move_to_prev_monitor", "status": "success"}

@app.get("/switch_to_workspace/{workspace}")
async def switch_to_workspace(workspace: int):
    await asyncio.to_thread(partial(daemon.wm.manager.switch_to_workspace, workspace))
    return {"operation": "switch_to_workspace", "status": "success"}

@app.get("/next_workspace")
async def next_workspace():
    await asyncio.to_thread(daemon.wm.manager.next_workspace)
    return {"operation": "next_workspace", "status": "success"}

@app.get("/prev_workspace")
async def prev_workspace():
    await asyncio.to_thread(daemon.wm.manager.prev_workspace)
    return {"operation": "prev_workspace", "status": "success"}

@app.get("/move_to_workspace/{workspace}")
async def move_to_workspace(workspace: int):
    await asyncio.to_thread(partial(daemon.wm.manager.move_to_workspace, workspace))
    return {"operation": "move_to_workspace", "status": "success"}

@app.get("/move_to_next_workspace")
async def move_to_next_workspace():
    await asyncio.to_thread(daemon.wm.manager.move_to_next_workspace)
    return {"operation": "move_to_next_workspace", "status": "success"}

@app.get("/move_to_prev_workspace")
async def move_to_prev_workspace():
    await asyncio.to_thread(daemon.wm.manager.move_to_prev_workspace)
    return {"operation": "move_to_prev_workspace", "status": "success"}

@app.get("/toggle_splash")
async def toggle_splash():
    await asyncio.to_thread(daemon.wm.manager.toggle_splash)
    return {"operation": "toggle_splash", "status": "success"}


def run_fastapi():
    # Function to run FastAPI server in a separate thread
    config = Config(app, host="0.0.0.0", port=9999)
    server = Server(config=config)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.create_task(server.serve())
    _logger.debug("Starting server...")
    loop.run_forever()


def stop_server(loop):
    # Function to stop the server gracefully
    _logger.debug("Stopping server...")
    loop.stop()

@contextmanager
def start_server():
    # Create a new thread for the FastAPI server
    global daemon
    fastapi_thread = threading.Thread(target=run_fastapi)
    fastapi_thread.daemon = True  # Set the thread as a daemon so it exits when the main program exits
    fastapi_thread.start()
    _logger.info("Creating daemon...")
    daemon = Daemon()
    try:
        yield daemon
    finally:
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(stop_server, loop)
        fastapi_thread.join()
