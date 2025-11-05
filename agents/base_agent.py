class BaseAgent:
    def __init__(
        self,
        model_name: str,
        generation_config: dict,
        llm_client,
    ):
        self.model_name = model_name
        self.generation_config = generation_config
        self.llm_client = llm_client

    def run(self):
        raise NotImplementedError()
