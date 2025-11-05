#!/bin/bash

# ==============================================================================
#                                 Configuration Area
# ==============================================================================

# Define the list of matchups to run. Format: "TvP", "TvZ", "PvZ", etc.
# T: Terran, P: Protoss, Z: Zerg
MATCHUPS=("TvT" "PvP" "ZvZ")

# Number of runs for each matchup
RUNS_PER_MATCHUP=20

# StarCraft II Parameters
MAP_NAME="Flat48"
DIFFICULTY="Harder"
AI_BUILD="RandomBuild"

# Model and API Parameters
MODEL_NAME="Qwen2.5-7B-Instruct"
BASE_URL="http://127.0.0.1:12001/v1"
API_KEY=""

# Agent Feature Toggles
ENABLE_PLAN="--enable_plan"
ENABLE_PLAN_VERIFIER="--enable_plan_verifier"
ENABLE_ACTION_VERIFIER="--enable_action_verifier"

# Interval between each launch (in seconds)
SLEEP_INTERVAL=10

# ==============================================================================
#                                 Script Body
# ==============================================================================

# Array to store the PIDs of all background processes
pids=()

# Function: Get the full race name from its first letter
get_full_race_name() {
    case "$1" in
        T) echo "Terran" ;;
        P) echo "Protoss" ;;
        Z) echo "Zerg" ;;
        *) echo "" ;; # Return an empty string on failure
    esac
}

# Iterate over all defined matchups
for matchup in "${MATCHUPS[@]}"; do
    # Use a regular expression to validate the "XvY" format
    if [[ ! "$matchup" =~ ^[TPZ]v[TPZ]$ ]]; then
        echo "Warning: Skipping invalid matchup format '$matchup'" >&2
        continue
    fi

    own_char="${matchup:0:1}"
    enemy_char="${matchup:2:1}"

    OWN_RACE=$(get_full_race_name "$own_char")
    ENEMY_RACE=$(get_full_race_name "$enemy_char")

    echo "------------------------------------------------------------"
    echo "Preparing to launch $RUNS_PER_MATCHUP runs for matchup [$matchup] ($DIFFICULTY $MAP_NAME $AI_BUILD)"
    echo "Model: ${MODEL_NAME} ($BASE_URL)"
    echo "------------------------------------------------------------"

    # Run the specified number of times for the current matchup
    for i in $(seq 1 $RUNS_PER_MATCHUP); do
        echo "=> Launching run $i / $RUNS_PER_MATCHUP for $matchup..."

        nohup python main.py \
            --player_name "${matchup}_benchmark" \
            --map_name "$MAP_NAME" \
            --difficulty "$DIFFICULTY" \
            --model "$MODEL_NAME" \
            --ai_build "$AI_BUILD" \
            --base_url "$BASE_URL" \
            --api_key "$API_KEY" \
            $ENABLE_PLAN \
            $ENABLE_PLAN_VERIFIER \
            $ENABLE_ACTION_VERIFIER \
            --own_race "$OWN_RACE" \
            --enemy_race "$ENEMY_RACE" &
        
        # Store the PID of the last background process
        pids+=($!)
        
        # Wait a moment to avoid high system load from starting too many processes at once
        sleep $SLEEP_INTERVAL
    done
done

echo "============================================================"
echo "All tasks have been launched in the background!"
echo "A total of ${#pids[@]} processes have been started."
echo "List of all process PIDs: ${pids[*]}"
echo ""
echo "You can use the following command to monitor their status:"
echo "ps -p ${pids[*]}"
echo ""
echo "If you want to terminate all these processes at once, use:"
echo "kill ${pids[*]}"
echo "============================================================"

# (Optional) If you want the script to wait for all background tasks to complete before exiting, uncomment the line below
# wait
