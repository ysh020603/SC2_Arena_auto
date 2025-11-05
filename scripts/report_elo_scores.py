from glob import glob
import random

# --- ELO 配置 ---
INITIAL_ELO = 1000
K_FACTOR = 32


def calculate_new_elos(elo_a, elo_b, score_a):
    """
    根据比赛结果计算两位玩家的新ELO分数。

    Args:
        elo_a (float): 玩家A的当前ELO分数。
        elo_b (float): 玩家B的当前ELO分数。
        score_a (float): 玩家A的实际得分 (1 for win, 0.5 for tie, 0 for loss)。

    Returns:
        tuple[float, float]: 玩家A和玩家B的新ELO分数。
    """
    # 1. 计算期望胜率
    expected_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
    expected_b = 1 - expected_a  # E_A + E_B = 1

    # 2. 玩家B的实际得分
    score_b = 1 - score_a

    # 3. 计算新ELO分数
    new_elo_a = elo_a + K_FACTOR * (score_a - expected_a)
    new_elo_b = elo_b + K_FACTOR * (score_b - expected_b)

    return new_elo_a, new_elo_b


# --- 数据加载 ---
# 使用通配符匹配所有对战日志
log_path_pattern = "logs/elo/*/Flat32/*/*/*/trace.json"
trace_files = glob(log_path_pattern)

# 打乱比赛顺序以避免潜在的顺序偏差
random.seed(100)
random.shuffle(trace_files)

print(f"找到 {len(trace_files)} 个对战日志文件。")

# ELO分数表，将动态填充
elo_scores = {}

# --- ELO 计算循环 ---
for trace_file in trace_files:
    try:
        # 从文件路径中解析元信息
        # 路径结构: logs/elo/Protoss/Flat32/{matchup}/{p1_model}/{timestamp}/trace.json
        # 例如: .../Flat32/Qwen3-8B v.s. deepseek-chat/Qwen3-8B/2025-07-24...
        meta_info = trace_file.replace("\\", "/").split("/")

        matchup_str = meta_info[-4]  # "Qwen3-8B v.s. deepseek-chat"
        p1_model = meta_info[-3]  # "Qwen3-8B" (本次对战的玩家1)

        # 从对战组合中找出玩家2
        all_models_in_matchup = matchup_str.split(" v.s. ")
        # 使用列表推导式找到另一个模型，比next()更安全，以防万一p1_model不在列表中
        p2_model_list = [m for m in all_models_in_matchup if m != p1_model]
        if not p2_model_list:
            print(
                f"警告: 无法在 '{matchup_str}' 中为玩家 '{p1_model}' 找到对手。跳过文件: {trace_file}"
            )
            continue
        p2_model = p2_model_list[0]

        # 动态发现并初始化新模型
        for model in [p1_model, p2_model]:
            if model not in elo_scores:
                elo_scores[model] = INITIAL_ELO

        # 获取当前ELO分数
        p1_elo = elo_scores[p1_model]
        p2_elo = elo_scores[p2_model]

        # 读取比赛结果
        with open(trace_file, "r", encoding="utf-8") as f:
            # 为了效率，可以逐行读取，而不是一次性读入整个可能很大的JSON文件
            score_p1 = None
            for line in f:
                if '"game_result"' in line:
                    if '"Victory"' in line:
                        score_p1 = 1.0
                    elif '"Tie"' in line:
                        score_p1 = 0.5
                    elif '"Defeat"' in line:
                        score_p1 = 0.0
                    break  # 找到结果后即可退出

        # 如果找到了比赛结果，则更新ELO分数
        if score_p1 is not None:
            new_p1_elo, new_p2_elo = calculate_new_elos(p1_elo, p2_elo, score_p1)
            elo_scores[p1_model] = new_p1_elo
            elo_scores[p2_model] = new_p2_elo
        else:
            print(f"警告: 在文件 {trace_file} 中未找到有效的 'game_result'。")

    except (IndexError, FileNotFoundError) as e:
        print(f"错误: 解析文件路径 '{trace_file}' 时出错: {e}。跳过。")


# --- 结果报告 ---
# 按ELO分数降序排序
sorted_scores = sorted(elo_scores.items(), key=lambda item: item[1], reverse=True)

# 打印格式化的排名报告
print("\n=== ELO Score Report ===")
if not sorted_scores:
    print("没有计算任何模型的ELO分数。")
else:
    # 计算名字和分数的最长长度以便对齐
    max_len_name = max(len(model) for model, score in sorted_scores)

    print(f"{'Rank':<5} | {'Model':<{max_len_name}} | {'ELO Score':>12}")
    print("-" * (5 + 3 + max_len_name + 3 + 12))

    for i, (model, score) in enumerate(sorted_scores):
        rank = i + 1
        print(f"{rank:<5} | {model:<{max_len_name}} | {score:>12.2f}")
