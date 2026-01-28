import time
import requests
import json
import uuid

COMFY_URL = "http://localhost:8188"
client_id = str(uuid.uuid4())

with open("text_to_image.json", "r") as f:
    prompt = json.load(f)

response = requests.post(
    f"{COMFY_URL}/prompt", json={"prompt": prompt, "client_id": client_id}, timeout=10
)

result = response.json()

if "prompt_id" in result:
    prompt_id = result["prompt_id"]
    print(f"The task has been submit prompt ID: {prompt_id}")

    history_url = f"{COMFY_URL}/history/{prompt_id}"
    while True:
        history_resp = requests.get(history_url)
        history_data = history_resp.json()

        if prompt_id in history_data:
            outputs = history_data[prompt_id]["outputs"]
            print(f"The task has finished, output message: {outputs}")
            break
        time.sleep(1)

    stats_response = requests.post(
        f"{COMFY_URL}/exec-timer/stat",
        json={
            "prompt_id": prompt_id,
        },
        timeout=10,
    )

    stats_response.raise_for_status()
    stats_dict = stats_response.json()

    assert stats_dict["prompt_id"] == prompt_id
    resp_msg = stats_dict["msg"]
    resp_code = stats_dict["code"]
    node_stats = stats_dict["data"]

    TABLE_HEADER = ["GLOBAL_STEP", "CLASS TYPE", "Execution Time(ms)", "Unique ID"]
    print(
        f"{TABLE_HEADER[0]:<30} {TABLE_HEADER[1]:<30} {TABLE_HEADER[2]:<30} {TABLE_HEADER[3]:<30}"
    )

    for key, value in node_stats.items():
        if key == "peak_memory_usage" or key == "total_time":
            print(f"{key:<30} {value:<30}")
        else:
            print(
                f"{key:<30} {value['class_type']:<30} {value['exec_time_ms']:<30} {value['unique_id']:<30}"
            )

else:
    print(f"Failed to submit task, {result.get('error', 'Unknown error')}")
