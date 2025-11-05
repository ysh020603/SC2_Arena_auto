#!/bin/bash

# --- Configuration ---
# 1. Set the total number of runs you want to execute.
TOTAL_RUNS=100

# 2. Define arrays for all available options.
MAP_OPTIONS=("Flat32" "Flat48" "Flat64")
DIFFICULTY_OPTIONS=("Medium" "MediumHard" "Hard" "Harder" "VeryHard")
AI_BUILD_OPTIONS=("RandomBuild" "Timing" "Rush" "Macro" "Power" "Air")
RACE_OPTIONS=("Terran" "Protoss" "Zerg")

MODEL_NAME="Qwen2.5-7B-Instruct"
BASE_URL="http://127.0.0.1:12001/v1"
API_KEY=""

# --- Main Loop ---
# Use a for loop to run N times.
for (( i=1; i<=TOTAL_RUNS; i++ ))
do
    echo "=================================================="
    echo "===> Starting run $i / $TOTAL_RUNS"
    echo "=================================================="

    # --- Select Configuration Randomly ---
    # Randomly select an element from an array.
    # Syntax: ARRAY[ $RANDOM % ${#ARRAY[@]} ]
    # $RANDOM is a random integer between 0 and 32767.
    # ${#ARRAY[@]} is the length of the array.
    # % is the modulo operator, ensuring the result is a valid array index.
    MAP=${MAP_OPTIONS[ $RANDOM % ${#MAP_OPTIONS[@]} ]}
    DIFFICULTY=${DIFFICULTY_OPTIONS[ $RANDOM % ${#DIFFICULTY_OPTIONS[@]} ]}
    AI_BUILD=${AI_BUILD_OPTIONS[ $RANDOM % ${#AI_BUILD_OPTIONS[@]} ]}
    OWN_RACE=${RACE_OPTIONS[ $RANDOM % ${#RACE_OPTIONS[@]} ]}
    ENEMY_RACE=${RACE_OPTIONS[ $RANDOM % ${#RACE_OPTIONS[@]} ]}

    # --- Execute Command ---
    PLAYER_NAME="sc2agent"

    python main.py \
        --player_name "${PLAYER_NAME}" \
        --map_name "${MAP}" \
        --difficulty "${DIFFICULTY}" \
        --model "${MODEL}" \
        --ai_build "${AI_BUILD}" \
        --enable_plan \
        --enable_plan_verifier \
        --enable_action_verifier \
        --enable_random_decision_interval \
        --own_race "${OWN_RACE}" \
        --enemy_race "${ENEMY_RACE}" \
        --base_url "${BASE_URL}" \
        --api_key "${API_KEY}"

done

echo "=================================================="
echo "All ${TOTAL_RUNS} runs have been completed."
echo "=================================================="
