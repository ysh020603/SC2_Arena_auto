# SC2Arena

### A StarCraft II Battle Arena for Large Language Models

Pit your Large Language Model agents against each other and the built-in StarCraft II AI in a dynamic, real-time strategy environment. `SC2Arena` provides a robust framework to test the strategic planning and execution capabilities of LLMs in one of the most complex RTS games ever made.

</div>

## ✨ Key Features

*   **LLM-Powered Agents:** Connect any powerful LLMs you want to try to control a player in StarCraft II.
*   **Human-like Reasoning:** Agents can perform high-level strategic planning, verify the validity of their plans, and self-correct their actions.
*   **Extensible Framework:** Easily add new agents, models, or custom game logic.
*   **Detailed Configuration:** Fine-tune every aspect of the match, from map and difficulty to race and AI build strategies.
*   **Benchmarking:** Test your agent against various built-in AI difficulty levels and builds to measure its performance.

## 🚀 Getting Started

Follow these steps to set up your local environment and start a match.

### 1. Install StarCraft II 🎮

You need a local installation of the game. The free Starter Edition is sufficient.

*   **Windows / macOS:**
    1.  Download and install the game from the [official StarCraft II website](https://starcraft2.blizzard.com/).
    2.  (Optional but recommended) In the Battle.net launcher settings, change the game language to English.

*   **Linux:**
    1.  Download the Linux game package from the [s2client-proto repository](https://github.com/Blizzard/s2client-proto?tab=readme-ov-file#linux-packages).
    2.  Set the `SC2PATH` environment variable to your installation directory.
        ```bash
        export SC2PATH="/path/to/StarCraftII"
        ```

### 2. Set Up Game Maps 🗺️

Download the necessary map packs to run experiments.

1.  Download the map packs from the [s2client-proto repository](https://github.com/Blizzard/s2client-proto?tab=readme-ov-file#map-packs). The **`Melee`** pack is required.
2.  Create a `Maps` folder inside your StarCraft II installation directory if it doesn't exist.
3.  Unzip `Melee.zip` into the `Maps` folder using the password `iagreetotheeula`. Your final directory structure should look like this:
    ```
    /path/to/StarCraftII/
    ├── Maps/
    │   ├── Melee/
    │   │   ├── Flat32.SC2Map
    │   │   ├── ... (other maps)
    ```

### 3. Configure the Agent 🤖

Set up the Python environment and API keys for your LLM agent.

1.  **Clone the repository and install dependencies:**
    ```bash
    git clone https://github.com/Anonymous/SC2Arena.git
    cd SC2Arena
    pip install -r requirements.txt
    ```

2.  **Configure API Keys:**
    Create a `.env` file by copying the template:
    ```bash
    cp .env_template .env
    ```
    Now, open the `.env` file and add your LLM provider's API key and base URL.
    ```env
    # .env
    API_KEY="your_api_key_here"
    BASE_URL="https://api.example.com/v1"
    ```
    Alternatively, you can set these as environment variables or pass them as command-line arguments.

## ▶️ Running a Battle

You are now ready to launch a match! Run `main.py` with your desired configuration.

```bash
python main.py \
    --map_name Flat32 \
    --difficulty Hard \
    --model Qwen2.5-32B-Instruct \
    --ai_build RandomBuild \
    --enable_plan \
    --enable_plan_verifier \
    --enable_action_verifier \
    --own_race Terran \
    --enemy_race Terran
```

### Command-Line Arguments

Here are some of the key parameters you can configure:

| Argument                 | Description                                                              | Default              |
| ------------------------ | ------------------------------------------------------------------------ | -------------------- |
| `--map_name`             | The name of the map to play on (e.g., `Flat32`, `Simple64`).             | `Flat32`             |
| `--difficulty`           | The difficulty of the built-in AI opponent.                              | `Hard`               |
| `--model`                | The name of the LLM to use for the agent.                                | `Qwen2.5-32B-Instruct` |
| `--ai_build`             | The build order for the built-in AI (e.g., `Rush`, `Macro`).             | `RandomBuild`        |
| `--own_race` / `--enemy_race` | The race for your agent and the opponent (`Terran`, `Protoss`, `Zerg`). | `Terran`             |
| `--enable_plan`          | Enables the high-level strategic planning module for the agent.          | `False`              |
| `--enable_plan_verifier` | Enables a module that verifies the logic of the generated plan.          | `False`              |
| `--enable_action_verifier`| Enables a module that verifies the validity of each generated action.    | `False`              |

> For a full list of available maps, races, and other settings, see `tools/constants.py`.

### Battle in ELO mode

You can launch a battle between two AI agents and calculate their ELO score.

Set up the necessary player information in `run_elo_template.py`. Then run:

```
python run_elo_template.py
```

## 🤝 How to Contribute

Contributions are welcome! Whether it's adding a new agent, improving documentation, or fixing a bug, we appreciate your help.

1.  **Fork** the repository.
2.  Create a new **branch** (`git checkout -b feature/your-feature-name`).
3.  Make your changes and **commit** them.
4.  Push to your branch (`git push origin feature/your-feature-name`).
5.  Open a **Pull Request**.

Please feel free to open an [issue](https://github.com/Anonymous/SC2Arena/issues) to report bugs or suggest new features.

---
*StarCraft II is a trademark of Blizzard Entertainment, Inc. This project is not affiliated with or endorsed by Blizzard Entertainment.*