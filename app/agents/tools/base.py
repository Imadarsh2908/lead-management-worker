"""
app/agents/tools/base.py
-------------------------
Abstract base class for all LangGraph agent tools.

Enforces SOLID principles:
  Single Responsibility: Each concrete tool handles ONE external concern.
  Open/Closed: Core execution wrapper is CLOSED for modification; tools
               are OPEN for extension by subclassing _execute_tool().
  Liskov Substitution: Any tool subclass can replace BaseAgentTool.

Why extend BaseTool (Langchain)?
  - Langchain's BaseTool handles tool_choice JSON formatting for the LLM.
  - It provides a consistent .invoke() interface across all tools.
  - Errors during tool execution are caught and returned as strings so
    the LLM can read the error and decide how to proceed (self-correction).
"""
import logging
from abc import abstractmethod
from typing import Type, Optional

from pydantic import BaseModel
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseAgentTool(BaseTool):
    """
    Base class for all agent tools. Provides:
      - Automatic input validation via args_schema
      - Structured JSON output
      - Centralized error handling (errors are returned as strings, not raised)
      - Audit logging hooks
    """

    # These MUST be defined by every subclass
    name: str
    description: str
    args_schema: Type[BaseModel]
    response_schema: Type[BaseModel]

    def _run(self, **kwargs) -> str:
        """
        Main execution wrapper called by the LangGraph agent runtime.
        
        Flow:
          1. Pydantic validates inputs automatically before this is called
          2. Calls _execute_tool() with the validated kwargs
          3. Validates the output against response_schema
          4. Returns a JSON string (LLM reads strings, not Python dicts)
          5. On ANY exception, returns a structured error JSON string
          6. On ANY exception, returns a structured error JSON string
             so the LLM can read the failure and self-correct
        """
        logger.info(f"[TOOL] Executing '{self.name}' with inputs: {kwargs}")

        try:
            result_model = self._execute_tool(**kwargs)

            # Runtime check to ensure the tool returns the expected schema type
            if not isinstance(result_model, self.response_schema):
                raise TypeError(
                    f"Tool '{self.name}' returned {type(result_model).__name__}, "
                    f"expected {self.response_schema.__name__}"
                )

            json_output = result_model.model_dump_json()
            logger.info(f"[TOOL] '{self.name}' completed successfully.")
            return json_output

        except Exception as e:
            # Return a structured error so the LLM can reason about it
            logger.error(f"[TOOL] '{self.name}' failed: {e}", exc_info=True)
            return f'{{"error": "{str(e)}", "tool": "{self.name}"}}'

    @abstractmethod
    def _execute_tool(self, **kwargs) -> BaseModel:
        """
        The actual business logic for this specific tool.
        Must return an instance of self.response_schema.
        """
        pass
