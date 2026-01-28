import time
import server
import execution
import inspect
import os
import json
from aiohttp import web
import csv
import torch

API_PATH = "/exec-timer/stat"
ENABLE_CSV_GEN = os.environ.get("EXEC_TIMER_ENABLE_CSV_GEN", True)
TIME_PRECISION = int(os.environ.get("EXEC_TIMER_TIME_PRECISION", "2"))
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "outputs")

API_ALLOW_ORIGIN = "*"

NODE_STATS = {}
WORKFLOW_INFO = {
    "start_time": None,
    "end_time": None,
    "prompt_id": None,
    "is_running": False,
    "peak_memory_usage": None,
}

GLOBAL_STEP = 1


def init_stats(prompt_id: str):
    global NODE_STATS, WORKFLOW_INFO, GLOBAL_STEP
    NODE_STATS = {}
    WORKFLOW_INFO = {
        "start_time": time.perf_counter(),
        "end_time": None,
        "prompt_id": prompt_id,
        "is_running": True,
        "peak_memory_usage": None,
    }
    GLOBAL_STEP = 1
    torch.cuda.reset_max_memory_allocated()
    print(
        f"\n[Exec_Timer] Workflow begins, prompt_id: {prompt_id}, initialize status..."
    )


def reset_stats():
    global NODE_STATS, WORKFLOW_INFO, GLOBAL_STEP
    NODE_STATS = {}
    WORKFLOW_INFO = {
        "start_time": None,
        "end_time": None,
        "prompt_id": None,
        "is_running": False,
        "peak_memory_usage": None,
    }
    GLOBAL_STEP = 1
    torch.cuda.reset_max_memory_allocated()


def get_accurate_time(raw_time: float, precision: int):
    if precision < 0:
        raise ValueError("The precision can't be smaller than zero.")

    if precision == 0:
        return int(raw_time)
    else:
        return round(raw_time, TIME_PRECISION)


def generate_csv_file(prompt_id: str):
    global WORKFLOW_INFO, NODE_STATS, OUTPUT_PATH
    try:
        prompt_id = WORKFLOW_INFO["prompt_id"]
        csv_filepath = os.path.join(OUTPUT_PATH, f"{prompt_id}.csv")
        with open(csv_filepath, "w") as file:
            csv_writer = csv.writer(file, delimiter=" ")
            csv_writer.writerow(
                ["GLOBAL_STEP", "CLASS TYPE", "Execution Time(ms)", "Unique ID"]
            )
            for global_step, node_stat in NODE_STATS.items():
                csv_writer.writerow(
                    [
                        global_step,
                        node_stat["class_type"],
                        node_stat["exec_time_ms"],
                        node_stat["unique_id"],
                    ]
                )

            workflow_execution_time_diff = (
                WORKFLOW_INFO["end_time"] - WORKFLOW_INFO["start_time"]
            ) * 1000
            workflow_execution_time_ms = get_accurate_time(
                workflow_execution_time_diff, TIME_PRECISION
            )
            csv_writer.writerow(
                ["Total Execution Time(ms)", workflow_execution_time_ms]
            )
            csv_writer.writerow(
                ["Peak Memory Usage(Bytes)", WORKFLOW_INFO["peak_memory_usage"]]
            )

        print(f"The csv file has been generated: {csv_filepath}")
    except Exception as e:
        print(f"[Exec_Timer] Failed to save csv: {str(e)}")


def generate_json_file(prompt_id: str):
    global NODE_STATS, WORKFLOW_INFO, OUTPUT_PATH

    node_stats_with_mem = NODE_STATS.copy()
    workflow_execution_time_diff = (
        WORKFLOW_INFO["end_time"] - WORKFLOW_INFO["start_time"]
    ) * 1000
    workflow_execution_time_ms = get_accurate_time(
        workflow_execution_time_diff, TIME_PRECISION
    )
    node_stats_with_mem["peak_memory_usage"] = WORKFLOW_INFO["peak_memory_usage"]
    node_stats_with_mem["total_time"] = workflow_execution_time_ms

    try:
        prompt_id = WORKFLOW_INFO["prompt_id"]
        json_filepath = os.path.join(OUTPUT_PATH, f"{prompt_id}.json")
        with open(json_filepath, "w") as file:
            json.dump(node_stats_with_mem, file)

        print(f"The json file has been generated: {json_filepath}")
    except Exception as e:
        print(f"[Exec_Timer] Failed to save json: {str(e)}")


async def send_workflow_timer_record(request):
    global NODE_EXEC_LOG, WORKFLOW_INFO, OUTPUT_PATH
    post_data = await request.json()
    prompt_id = post_data.get("prompt_id")
    if prompt_id == WORKFLOW_INFO["prompt_id"] and WORKFLOW_INFO["is_running"]:
        return_code = 200
        return_msg = "Succeed to find the target prompt, but it is still running."
        node_stats = {}
    else:
        try:
            json_filepath = os.path.join(OUTPUT_PATH, f"{prompt_id}.json")
            with open(json_filepath, "r") as file:
                node_stats = json.load(file)
            return_code = 200
            return_msg = "Succeed to get the workflow execution infomation."
        except Exception as e:
            print(f"Failed to open {json_filepath}: {str(e)}")
            return_code = 500
            return_msg = f"Failed to get status info for {prompt_id}"
            node_stats = {}

    response_data = {
        "code": return_code,
        "msg": return_msg,
        "prompt_id": prompt_id,
        "data": node_stats,
        "timestamp": int(time.time() * 1000),
    }
    headers = {
        "Access-Control-Allow-Origin": API_ALLOW_ORIGIN,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    return web.json_response(response_data, headers=headers)


def register_api_route():
    try:
        server.PromptServer.instance.app.add_routes(
            [
                # web.get(API_PATH, get_workflow_timer_record),
                web.post(API_PATH, send_workflow_timer_record),
                # web.options(API_PATH, get_workflow_timer_record)
            ]
        )
        print(
            f"[Exec_Timer] API registered | access address: http://0.0.0.0:8188{API_PATH}"
        )
    except Exception as e:
        print(f"[Exec_Timer] Failed to register API: {str(e)}")


origin_execute = execution.execute

# async
if inspect.iscoroutinefunction(origin_execute):

    async def hooked_execute(
        server_inst,
        dynprompt,
        caches,
        current_item,
        extra_data,
        executed,
        prompt_id,
        execution_list,
        pending_subgraph_results,
        pending_async_nodes,
        *args,
        **kwargs,
    ):
        global NODE_STATS, WORKFLOW_INFO, GLOBAL_STEP
        unique_id = current_item

        if not WORKFLOW_INFO["is_running"]:
            init_stats(prompt_id)

        try:
            node_info = dynprompt.get_node(unique_id)
            class_type = node_info.get("class_type", "Unknown Class")
            display_id = dynprompt.get_display_node_id(unique_id)
        except Exception:
            class_type = "Unknown Class"
            display_id = "Unknown Id"

        node_start_time = time.perf_counter()

        result = await origin_execute(
            server_inst,
            dynprompt,
            caches,
            current_item,
            extra_data,
            executed,
            prompt_id,
            execution_list,
            pending_subgraph_results,
            pending_async_nodes,
            *args,
            **kwargs,
        )

        # Caculate the execution time
        node_end_time = time.perf_counter()
        exec_time_ms = (node_end_time - node_start_time) * 1000
        exec_time_ms = get_accurate_time(exec_time_ms, TIME_PRECISION)

        NODE_STATS[str(GLOBAL_STEP)] = {
            "display_id": display_id,
            "class_type": class_type,
            "start_time": node_start_time,
            "exec_time_ms": exec_time_ms,
            "unique_id": unique_id,
        }
        GLOBAL_STEP += 1

        print(
            f"[Exec_Timer] Node Exec Finished | Class Type{class_type:25s} | Front ID: {display_id:5s} | Execution Time: {exec_time_ms} ms | Unique_id: {unique_id}"
        )

        return result
else:

    def hooked_execute(
        server_inst,
        dynprompt,
        caches,
        current_item,
        extra_data,
        executed,
        prompt_id,
        execution_list,
        pending_subgraph_results,
        *args,
        **kwargs,
    ):
        global NODE_STATS, WORKFLOW_INFO, GLOBAL_STEP
        unique_id = current_item

        if not WORKFLOW_INFO["is_running"]:
            init_stats(prompt_id)

        try:
            node_info = dynprompt.get_node(unique_id)
            class_type = node_info.get("class_type", "Unknown Class")
            display_id = dynprompt.get_display_node_id(unique_id)
        except Exception:
            class_type = "Unknown Class"
            display_id = "Unknown ID"

        node_start_time = time.perf_counter()

        result = origin_execute(
            server_inst,
            dynprompt,
            caches,
            current_item,
            extra_data,
            executed,
            prompt_id,
            execution_list,
            pending_subgraph_results,
            *args,
            **kwargs,
        )

        node_end_time = time.perf_counter()
        exec_time_ms = (node_end_time - node_start_time) * 1000
        exec_time_ms = get_accurate_time(exec_time_ms, TIME_PRECISION)

        # 存储统计数据
        NODE_STATS[str(GLOBAL_STEP)] = {
            "display_id": display_id,
            "class_type": class_type,
            "start_time": node_start_time,
            "exec_time_ms": exec_time_ms,
            "unique_id": unique_id,
        }
        GLOBAL_STEP += 1

        print(
            f"[Exec_Timer] Node Exec Finished | Class Type{class_type:25s} | Front ID: {display_id:5s} | Execution Time: {exec_time_ms} ms | Unique_id: {unique_id}"
        )

        return result


execution.execute = hooked_execute


origin_send_sync = server.PromptServer.send_sync


def hooked_send_sync(self, event, data, sid=None):
    global WORKFLOW_INFO, ENABLE_CSV_GEN
    origin_send_sync(self, event, data, sid)

    # The signal that indicate the workflow ends: event = 'executing' && data.node = None
    if (
        event == "executing"
        and data
        and data.get("node") is None
        and WORKFLOW_INFO["is_running"]
    ):
        WORKFLOW_INFO["end_time"] = time.perf_counter()
        workflow_execution_time_tmsp = (
            WORKFLOW_INFO["end_time"] - WORKFLOW_INFO["start_time"]
        ) * 1000
        workflow_execution_time_ms = round(workflow_execution_time_tmsp, TIME_PRECISION)
        print(
            f"The total excution time of the workflow {WORKFLOW_INFO['prompt_id']} is {workflow_execution_time_ms}"
        )

        WORKFLOW_INFO["peak_memory_usage"] = torch.cuda.max_memory_allocated()
        print(
            f"[Exec-Timer] The max memory usage is : {WORKFLOW_INFO['peak_memory_usage']} MB"
        )
        prompt_id = WORKFLOW_INFO["prompt_id"]
        generate_json_file(prompt_id)
        if ENABLE_CSV_GEN:
            generate_csv_file(prompt_id)
        reset_stats()


server.PromptServer.send_sync = hooked_send_sync

register_api_route()

print(
    """
                    _____________  _____             _____      __     
                / ___/ __/ __/ / ___/__  _______ / __/ | /| / /     
                / /___\ \/ _/  / /__/ _ \/ __/ -_)\ \ | |/ |/ /      
            _____\___/___/___/__\___/\___/_/ _\__/___/_|__/|__/___  __
            /_  __/  _/  |/  / __/ _ \  / _ \/ /  / / / / ___/  _/ |/ /
            / / _/ // /|_/ / _// , _/ / ___/ /__/ /_/ / (_ // //    / 
            /_/ /___/_/  /_/___/_/|_| /_/  /____/\____/\___/___/_/|_/  
    """
)

print(
    f"[Exec_Timer] The pure backend plugin Exec_Timer load successfully | Generate CSV File:{'True' if ENABLE_CSV_GEN else 'False'} | Time Precision: {TIME_PRECISION}"
)
