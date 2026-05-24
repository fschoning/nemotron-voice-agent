import os
import json
import asyncio
import google.generativeai as genai
from loguru import logger
from apps.sanitiser import sanitise_question

class ThinkingBridge:
    def __init__(self, mcp_tools_cache, mcp_call_callback, session_data=None):
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        
        # Initialize context and active task references for Option 2 async execution
        self.context = None
        self.pipeline_task = None
        self._active_analysis_task = None
        
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
You are the Deep Thinking Vedic Astrology Brain (Gemini Pro Latest).
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
        
        # 3. Setup Guardrail Prompt & History
        if not session_data:
            raise ValueError("FATAL: session_data is missing in ThinkingBridge! Cannot initialize without CRM session context.")
            
        system_instruction = session_data.get("brainPrompt")
        if not system_instruction or not system_instruction.strip():
            raise ValueError("FATAL: 'brainPrompt' is missing or empty in session_data for ThinkingBridge!")
            
        self.guardrail_prompt = session_data.get("guardrailPrompt")
        if not self.guardrail_prompt or not self.guardrail_prompt.strip():
            raise ValueError("FATAL: 'guardrailPrompt' is missing or empty in session_data for ThinkingBridge!")
            
        logger.info("✅ Brain prompt and Guardrail prompt successfully loaded into ThinkingBridge.")
        history = []
        
        persons = session_data.get("persons", [])
        for person in persons:
            primed_analysis = person.get("primedAnalysis", None)
            if primed_analysis and "analysis" in primed_analysis:
                history.append({"role": "user", "parts": [f"Pre-call analysis for {person.get('firstName')}:\n{primed_analysis['analysis']}"]})
                history.append({"role": "model", "parts": ["Acknowledged. I have the pre-call analysis in my context."]})

        self.thinking_model = genai.GenerativeModel(
            model_name='gemini-pro-latest',
            system_instruction=system_instruction,
            tools=self.gemini_tools if self.gemini_tools else None
        )
        self.chat = self.thinking_model.start_chat(history=history)

    def set_pipeline_context(self, context, pipeline_task):
        """Saves references to the LLMContext and PipelineTask for out-of-band updates."""
        self.context = context
        self.pipeline_task = pipeline_task
        logger.info("📡 ThinkingBridge: Bound pipeline context and task references successfully.")

    async def handle_request_analysis(self, params):
        """Called by the Pipecat Voice pipeline when Flash uses the request_analysis tool."""
        question = params.arguments.get("question", "")
        logger.info(f"🤔 Flash requested analysis: {question}")
        
        # Start the background deep-thinking calculation coroutine (non-blocking)
        self._active_analysis_task = asyncio.create_task(
            self._run_background_pro_analysis(question)
        )
        
        # Return immediately to the front-end LLM.
        # This prompts the front-end LLM to formulate an immediate, in-character filler phrase,
        # and gracefully finish its turn.
        await params.result_callback({
            "result": (
                "Deep analysis initiated in the background. Please warmly explain to the client that "
                "you are consulting the deep alignments and birth charts, and that you will share the "
                "findings in just a moment. Once you explain this, immediately finish your turn."
            )
        })

    async def _run_background_pro_analysis(self, question):
        """Background calculation task running the heavy Gemini Pro / MCP tools."""
        try:
            logger.info("🧠 Brain background calculation started...")
            
            # 1. Run the Sanitiser model (via asyncio.to_thread to keep the main event loop completely free)
            sanitised = await asyncio.to_thread(
                sanitise_question,
                question,
                self.guardrail_prompt
            )
            
            if sanitised.startswith("BLOCKED:"):
                logger.warning(f"🚫 Question blocked by sanitiser in background: {sanitised}")
                rejection_reason = sanitised.split(":")[1]
                msg = self._get_block_rejection_message(rejection_reason)
                
                # Inject the block message and trigger LLM turn
                if self.context:
                    self.context.messages.append({
                        "role": "system",
                        "content": (
                            f"The client's question was flagged as prohibited. You must politely refuse "
                            f"to answer the question and direct them appropriately. Use this exact reasoning: "
                            f"'{msg}'. Speak warmly and professionally in your unique character."
                        )
                    })
                if self.pipeline_task:
                    from pipecat.frames.frames import LLMRunFrame
                    await self.pipeline_task.queue_frames([LLMRunFrame()])
                return

            logger.info(f"🤔 Sanitised query passing to Pro: {sanitised}")
            
            # 2. Run the Pro model (via asyncio.to_thread)
            response = await asyncio.to_thread(
                self.chat.send_message,
                sanitised,
                tools=self.gemini_tools if self.gemini_tools else None,
                request_options={"timeout": 600.0}
            )
            
            # Process potential tool calls in a loop until we get text
            while self._get_function_calls(response):
                tool_results = []
                for fc in self._get_function_calls(response):
                    sanitized_name = fc.name
                    original_name = self.tool_name_map.get(sanitized_name, sanitized_name)
                    args = dict(fc.args)
                    
                    logger.info(f"🧠 Pro called tool in background: {original_name} with {args}")
                    try:
                        mcp_res = await asyncio.wait_for(
                            self.mcp_call_callback(original_name, args),
                            timeout=300.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"❌ MCP Tool {original_name} timed out after 300 seconds.")
                        mcp_res = "Error: Tool execution timed out after 300 seconds."
                    except Exception as e:
                        logger.error(f"❌ Error executing MCP tool {original_name}: {e}")
                        mcp_res = f"Error executing tool: {e}"
                    
                    # Gemini expects the result as a dict or coerced structure
                    tool_results.append({
                        "function_response": {
                            "name": sanitized_name,
                            "response": {"result": mcp_res}
                        }
                    })
                
                # Send the tool results back to the model (via asyncio.to_thread)
                response = await asyncio.to_thread(
                    self.chat.send_message,
                    tool_results,
                    request_options={"timeout": 600.0}
                )

            final_text = response.text
            logger.info(f"🧠 Pro answered in background: {final_text}")
            
            # 3. Inject the result into the front-end LLM context messages history
            if self.context:
                self.context.messages.append({
                    "role": "system",
                    "content": (
                        f"The deep astrological calculation has finished. Here is the raw data and findings: "
                        f"'{final_text}'. You must now translate and explain these findings to the client in "
                        f"detail in your unique character/personality! Speak warm and engagingly. Do not read raw data dryly."
                    )
                })
                logger.info("📝 Successfully injected brain findings into front-end LLM context.")
            
            # 4. Trigger a new LLM generation turn on the front-end model to deliver the text in character
            if self.pipeline_task:
                from pipecat.frames.frames import LLMRunFrame
                await self.pipeline_task.queue_frames([LLMRunFrame()])
                logger.info("📡 Successfully queued LLMRunFrame downstream.")
                
        except asyncio.CancelledError:
            logger.warning("⚠️ Background analysis task was cancelled due to user speech/interruption.")
        except Exception as e:
            logger.error(f"❌ Error in background Pro analysis: {e}")
            if self.context:
                self.context.messages.append({
                    "role": "system",
                    "content": (
                        "The deep astrological calculations encountered a brief planetary alignment issue (timeout). "
                        "Please politely apologize to the client, mention a temporary chart eclipse, and ask them a warm follow-up."
                    )
                })
            if self.pipeline_task:
                from pipecat.frames.frames import LLMRunFrame
                await self.pipeline_task.queue_frames([LLMRunFrame()])

    def _get_function_calls(self, resp):
        calls = []
        if not resp or not resp.candidates:
            return calls
        for candidate in resp.candidates:
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                fc = getattr(part, 'function_call', None)
                if fc and fc.name:
                    calls.append(fc)
        return calls

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
