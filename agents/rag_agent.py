from agents.base_agent import BaseAgent
from tools.format import extract_code, construct_ordered_list

import json
import requests

RAG_URL = "http://172.18.30.165:12005/search"

rag_extract_query_prompt = """
You are a top-tier StarCraft II assitant. Given a StarCraft II knowledge database and current game state, your task is to propose some useful queries about the game state.

<game_state>
%s
</game_state>

Requirements:
1. These queries should be knowledge related and helpful for the player to make decisions.
2. Instead of some simple or straightforward questions, the queries should be confused for you.
3. We don't care how to send SCV to gather minerals distributedly.
4. Please provide a list of queries (no more than 3) in JSON format wrapped with triple backticks:
```
[
    "<query_1>",
    "<query_2>",
    ...
]
```
""".strip()

rag_summary_prompt = """
You are a top-tier StarCraft II assitant. Given a document reference and a query, your task is to provide a summary for the query based on the document.

<document>
%s
</document>

<query>
%s
</query>

Requirements:
1. The summary should be concise and informative, which is no more than 2 sentences.
2. Please provide the summary starting with <summary> and ending with </summary>.
""".strip()


class RagAgent(BaseAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.think = {}

    def get_queries(self, obs_text: str):
        prompt = rag_extract_query_prompt % obs_text
        response, messages = self.llm_client.call(prompt=prompt, **self.generation_config, need_json=True)

        queries = extract_code(response)
        queries = json.loads(queries)
        assert isinstance(queries, list)

        self.think["queries_response"] = response
        self.think["queries"] = queries

        return queries

    def get_summary(self, query: str, document: str):
        prompt = rag_summary_prompt % (document, query)
        response, messages = self.llm_client.call(prompt=prompt, **self.generation_config, need_json=False)

        summary = response.split("<summary>")[-1].split("</summary>")[0].strip()

        self.think["summaries"].append(
            {
                "query": query,
                "document": document,
                "response": response,
                "summary": summary,
            }
        )

        return summary

    def run(self, obs_text: str):
        self.think = {}

        queries = self.get_queries(obs_text)

        summaries = []
        self.think["summaries"] = []
        for query_text in queries:
            document = requests.post(
                url=RAG_URL,
                json={"query": query_text, "n": 2},
            ).json()[0][
                "content"
            ][:4096]
            summary = self.get_summary(query_text, document)
            summaries.append(summary)

        summary_text = construct_ordered_list(summaries)

        return summary_text, self.think
