import glob
import json
import os
import numpy as np
import matplotlib.pyplot as plt
import math
from collections import Counter
from tqdm import tqdm
from typing import List
from sklearn.preprocessing import StandardScaler
from collections import defaultdict
import random


def get_order_of_magnitude(num):
    if num == 0:
        return 0
    return 10 ** int(math.log10(abs(num)))


def draw_list(data: List, title: str = "", bins="auto"):
    """
    绘制列表中数据的分布情况

    参数:
        data (List): 包含数据的列表
    """
    plt.figure(figsize=(12, 6), dpi=300)

    # 创建子图布局
    plt.subplot(1, 2, 1)  # 左边是直方图
    plt.hist(data, bins=bins, color="skyblue", edgecolor="black", alpha=0.7)
    plt.xlabel("Value")
    plt.ylabel("Frequency")
    plt.grid(True, linestyle="--", alpha=0.5)

    plt.subplot(1, 2, 2)  # 右边是箱线图
    plt.boxplot(
        data,
        vert=True,
        patch_artist=True,
        boxprops=dict(facecolor="lightgreen"),
        medianprops=dict(color="red"),
    )
    plt.xticks([1], ["data"])
    plt.grid(True, linestyle="--", alpha=0.5)

    plt.suptitle(title, fontsize=14)
    plt.tight_layout()
    os.makedirs("imgs", exist_ok=True)
    plt.savefig("imgs/" + title.replace(" ", "_") + ".png")


trace_files = []
for folders in [
    "logs/sc2agent_0731/Flat48_Medium*/*/*/trace.json",
    "logs/sc2agent_0731/Flat48_MediumHard*/*/*/trace.json",
    # "logs/PvP_benchmark/*/deepseek-chat/*/trace.json",
    # "logs/ZvZ_benchmark/*/deepseek-chat/*/trace.json",
    # "logs/TvT_benchmark/*/deepseek-chat/*/trace.json",
]:
    trace_files.extend(glob.glob(folders))

plan_time_cnter = Counter()
action_time_cnter = Counter()
action_counter = Counter()

sft_trace = []

score_items = [
    "unit_mineral_value_score",
    "unit_vespene_value_score",
    "structure_mineral_value_score",
    "structure_vespene_value_score",
    "supply_army_score",
    "supply_workers_score",
]

score_hist = {item: [] for item in score_items}
score_scalers = {item: StandardScaler() for item in score_items}


def get_step_value_diff(s1, s2):
    score = {}
    for item in score_items:
        key = item.replace("_score", "")
        score[item] = s2[key] - s1[key]
        iteration_diff = (s2["iteration"] - s1["iteration"]) / 10
        score[item] *= 0.95**iteration_diff
    return score


def add_dict(d1, d2):
    assert d1.keys() == d2.keys()
    res = {}
    for item in d1.keys():
        res[item] = d1[item] + d2[item]
    return res


def process_trace(trace_list: List):
    for i, trace in enumerate(trace_list):
        if (
            "obs" not in trace
            or "plans" not in trace
            or "actions" not in trace
            or len(trace["plans"]) == 0
            or len(trace["actions"]) == 0
            or len(trace["actions"]) != len(trace["valid_actions"])
            or len(trace["plans"]) > 5
            or '"error_number": 0' not in trace["plan_think"][-1][-1]
        ):
            continue
        score = {item: 0.0 for item in score_items}
        for next_i in range(i + 1, i + 16):
            if next_i == len(trace_list):
                break
            step_score = get_step_value_diff(trace_list[i], trace_list[next_i])
            score = add_dict(score, step_score)
        for item in score_items:
            score_hist[item].append(score[item])
        sft_trace.append(
            {
                **score,
                **trace,
            }
        )

# for RACE in ["Terran", "Protoss", "Zerg"]
RACE = "Protoss"

n_trace = 0
for trace_file in tqdm(trace_files):
    # tqdm.write(f"Processing {trace_file}...")
    config_file = trace_file.replace("trace.json", "config.json")
    with open(config_file, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    if config_data["own_race"] != RACE:
        continue

    with open(trace_file, "r", encoding="utf-8") as f:
        trace_data = json.load(f)
    trace_list = list(trace_data.values())
    if "game_result" not in trace_list[-1] or trace_list[-1]["game_result"] != "Victory":
        continue
    trace_list = trace_list[:-1]
    process_trace(trace_list)

    n_trace += 1

print(f"Found {n_trace} trace files.")

for item in score_hist:
    x = score_hist[item]
    x = np.array(x).reshape(-1, 1)
    x = score_scalers[item].fit_transform(x)
    x = x.reshape(-1)
    draw_list(x, title=item)
    score_hist[item] = x

for i in range(len(sft_trace)):
    sft_trace[i]["stand_score"] = sum([score_hist[item][i] for item in score_hist]) * (
        1.1 ** sft_trace[i]["n_visible_enemy_units"]
    )

# Before filter
draw_list(
    [s["stand_score"] for s in sft_trace], title="Original Standard Score Distribution"
)
draw_list(
    [s["time_seconds"] for s in sft_trace], title="Original Time Distribution", bins=30
)
draw_list(
    [s["n_visible_enemy_units"] for s in sft_trace],
    title="Original n_visible_enemy_units Distribution",
)

min_score = 1.5
window_size = 30  # 窗口大小（秒）
top_k = 60  # 每个窗口最多保留多少条

sft_trace = [data for data in sft_trace if data["stand_score"] > min_score]

# 1) 按窗口编号分桶
buckets = defaultdict(list)
for sample in sft_trace:
    t = sample["time_seconds"]
    # 如果你的时间点是 1-30s、31-60s...，就用 (t-1)//30
    win_id = (t - 1) // window_size
    buckets[win_id].append(sample)

# 2) 每个桶内部按 stand_score 排序，选前 top_k
new_sft = []
for win_id in sorted(buckets.keys()):
    group = buckets[win_id]
    group.sort(key=lambda x: x["stand_score"], reverse=True)
    new_sft.extend(group[:top_k])

# 3) 替换回 sft_trace
sft_trace = new_sft
print(f"SFT data score > min_score:", len(sft_trace))

# After filter
draw_list(
    [s["stand_score"] for s in sft_trace], title="Filtered Standard Score Distribution"
)
draw_list(
    [s["time_seconds"] for s in sft_trace], title="Filtered Time Distribution", bins=30
)
draw_list(
    [s["n_visible_enemy_units"] for s in sft_trace],
    title="Filtered n_visible_enemy_units Distribution",
)

n_plan = 0
n_plan_critic = 0
n_action = 0

plan_turn_cnter = Counter()
plan_critic_turn_cnter = Counter()
action_turn_cnter = Counter()

sft_data = []
for trace in sft_trace:
    # For plan
    sft_data.append({"conversations": trace["plan_chat_history"][-2]})
    plan_turn_cnter.update([len(trace["plan_chat_history"][-2])])
    plan_time_cnter.update([trace["time_seconds"]])
    n_plan += 1

    if random.random() > 0.5:
        sft_data.append({"conversations": trace["plan_chat_history"][-1]})
        plan_critic_turn_cnter.update([len(trace["plan_chat_history"][-1])])
        plan_time_cnter.update([trace["time_seconds"]])
        n_plan_critic += 1

    plan_time_cnter.update([trace["time_seconds"]])
    if random.random() > 0.5:
        if len(trace["plan_chat_history"]) > 2:
            sft_data.append({"conversations": trace["plan_chat_history"][-3]})
            plan_critic_turn_cnter.update([len(trace["plan_chat_history"][-3])])
            plan_time_cnter.update([trace["time_seconds"]])
            n_plan_critic += 1
    # For action
    sft_data.append({"conversations": trace["action_chat_history"][-1]})
    action_turn_cnter.update([len(trace["action_chat_history"][-1])])
    action_time_cnter.update([trace["time_seconds"]])
    n_action += 1
    action_counter.update([action["action"] for action in trace["actions"]])

print("n_plan:", n_plan)
print("n_plan_critic:", n_plan_critic)
print("n_action:", n_action)
print("Total SFT sample:", len(sft_data))
print(f"Plan turn counts: {plan_turn_cnter}")
print(f"Plan critic turn counts: {plan_critic_turn_cnter}")
print(f"Action turn counts: {action_turn_cnter}")

with open(f"sc2-ds-{RACE}.json", "w", encoding="utf-8") as f:
    sft_data_str = json.dumps(sft_data, indent=4, ensure_ascii=False)
    sft_data_str = sft_data_str.replace('"role": "user",', '"from": "human",')
    sft_data_str = sft_data_str.replace('"role": "assistant",', '"from": "gpt",')
    sft_data_str = sft_data_str.replace('"content":', '"value":')
    f.write(sft_data_str)

# plot actions with hbar chart
import matplotlib.pyplot as plt

plt.figure(figsize=(10, 5))
sorted_actions = action_counter.most_common()
plt.barh(
    [action[0] for action in sorted_actions],
    [action[1] for action in sorted_actions],
    color="green",
    alpha=0.7,
)
plt.title("Action Distribution")
plt.xlabel("Count")
plt.ylabel("Action")
plt.tight_layout()
plt.savefig("imgs/action_distribution.png", dpi=300)
