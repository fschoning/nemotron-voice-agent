import os
import json
import asyncio
import google.generativeai as genai
from google.generativeai.types import content_types
from loguru import logger
from sanitiser import sanitise_question

class ThinkingBridge:
    def __init__(self, mcp_tools_cache, mcp_call_callback, session_data=None):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        
        # 1. Convert MCP tools to Gemini SDK Tool definitions
        self.mcp_call_callback = mcp_call_callback
        self.gemini_tools = []
        self.tool_name_map = {}
        
        for tool in mcp_tools_cache:
            original_name = tool.name
            sanitized_name = original_name.replace("-", "_")
            self.tool_name_map[sanitized_name] = original_name
            
            self.gemini_tools.append({
                "function_declarations": [
                    {
                        "name": sanitized_name,
                        "description": tool.description,
                        "parameters": tool.inputSchema if tool.inputSchema and "type" in tool.inputSchema else {"type": "OBJECT", "properties": {}}
                    }
                ]
            })

        # 2. Build system instruction with guardrails and session context
        system_instruction = """
You are the Deep Thinking Vedic Astrology Brain (Gemini 3.1 Pro).
You have access to Jyotish MCP tools. Use them to calculate Native Charts, Dashas, Transits, and Compatibility.

## GUARDRAILS - PROHIBITED TOPICS (LAYER 3)
You must REFUSE to analyse the following topics, even if directly asked:
- Death, mortality, timing of death
- Severe health diagnosis/prognosis
- Divorce/marital breakdown predictions
- Mental health diagnosis, therapy, suicide
- Medical advice, drug recommendations
- Legal advice, court outcomes
- Guaranteed financial returns
- Pregnancy/fertility predictions

If any of these are requested, output a polite refusal directing them to a relevant human professional.
"""
        
        # 3. Setup history
        history = []
        if session_data:
            persons = session_data.get("persons", [])
            for person in persons:
                primed_analysis = person.get("primedAnalysis", None)
                if primed_analysis and "analysis" in primed_analysis:
                    history.append({"role": "user", "parts": [f"Pre-call analysis for {person.get('firstName')}:\n{primed_analysis['analysis']}"]})
                    history.append({"role": "model", "parts": ["Acknowledged. I have the pre-call analysis in my context."]})

        self.thinking_model = genai.GenerativeModel(
            model_name='gemini-3.1-pro',
            system_instruction=system_instruction,
            tools=self.gemini_tools if self.gemini_tools else None
        )
        self.chat = self.thinking_model.start_chat(history=history)

    async def handle_request_analysis(self, params):
        """Called by the Pipecat Voice pipeline when Flash uses the request_analysis tool."""
        question = params.arguments.get("question", "")
        logger.info(f"🤔 Flash requested analysis: {question}")
        
        # Layer 2 Guardrail: Sanitiser
        sanitised = sanitise_question(question)
        if sanitised.startswith("BLOCKED:"):
            logger.warning(f"🚫 Question blocked by sanitiser: {sanitised}")
            msg = self._get_block_rejection_message(sanitised.split(":")[1])
            await params.result_callback({"result": msg})
            return

        logger.info(f"🤔 Sanitised query passing to Pro: {sanitised}")
        
        try:
            # Send to 3.1 Pro. We manually process tool calls since we have async external MCP tools
            response = self.chat.send_message(sanitised, tools=self.gemini_tools)
            
            # Process potential tool calls in a loop until we get text
            while response.function_calls:
                tool_results = []
                for fc in response.function_calls:
                    sanitized_name = fc.name
                    original_name = self.tool_name_map.get(sanitized_name, sanitized_name)
                    args = dict(fc.args)
                    
                    logger.info(f"🧠 Pro called tool: {original_name} with {args}")
                    mcp_res = await self.mcp_call_callback(original_name, args)
                    
                    # Gemini expects the result as a dict
                    tool_results.append(
                        content_types.Part.from_function_response(
                            name=sanitized_name,
                            response={"result": mcp_res}
                        )
                    )
                
                # Send the tool results back to the model
                response = self.chat.send_message(tool_results)

            final_text = response.text
            logger.info(f"🧠 Pro answered: {final_text}")
            await params.result_callback({"result": final_text})
            
        except Exception as e:
            logger.error(f"❌ Error in Thinking Bridge: {e}")
            await params.result_callback({"result": "I'm having trouble connecting to my deeper knowledge base right now. Let's stick to what we know for a moment."})

    def _get_block_rejection_message(self, category: str) -> str:
        messages = {
            "DEATH": "For health-related concerns, please consult a qualified medical professional.",
            "HEALTH": "Astrological insights are not medical advice. Please consult a healthcare provider.",
            "DIVORCE": "For relationship guidance, please consult a qualified counsellor or therapist.",
            "MENTAL_HEALTH": "For mental health support, please reach out to a licensed mental health professional.",
            "MEDICAL": "Please consult your doctor or pharmacist for medical advice.",
            "LEGAL": "For legal matters, please consult a qualified legal professional.",
            "FINANCIAL": "For financial decisions, please consult a certified financial advisor.",
            "FERTILITY": "For fertility concerns, please consult a reproductive health specialist."
        }
        return messages.get(category, "I cannot address this topic in the consultation.")
