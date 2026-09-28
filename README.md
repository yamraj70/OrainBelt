# Orianbelt v1.3

Orianbelt v1.3 is a local-first, multi-domain AI runtime for GGUF language models. It combines one shared base model with your own TXT, PDF, and DOCX knowledge and can automatically decide which knowledge domain should answer a question.

> Recommended current Windows environment: Windows 10/11, 64-bit Python 3.12, PowerShell, and a GGUF instruction model.

---

## What Orianbelt v1.3 can do

- Run a local GGUF instruction model with `llama-cpp-python`.
- Create multiple knowledge profiles such as BANKING, PROGRAMMING, LEGAL, HR, etc.
- Let all profiles share one GGUF file instead of duplicating a multi-GB model.
- Learn/index `.txt`, `.pdf`, and `.docx` documents without retraining the base model.
- Automatically route questions to the most relevant profile.
- Retrieve from up to two domains for cross-domain questions.
- Fall back to `GENERAL` pretrained model knowledge when no configured domain is relevant.
- Keep conversation history in an interactive CLI session.
- Show routing with `/route`.
- Show retrieved document evidence with `/sources`.
- Force a profile when automatic routing is not wanted.
- Serve the same system through a local FastAPI API.
- Accept `"model": "auto"` through the API.

Orianbelt v1.3 does **not** yet train LoRA adapters or fine-tune the base model. For now, domain facts are added through `learn`/RAG.

---

# Architecture

```text
                         User / API
                             |
                             v
                     Orianbelt Router
                             |
                +------------+-------------+
                |            |             |
                v            v             v
             BANKING     PROGRAMMING     LEGAL ...
                |            |             |
             Knowledge    Knowledge      Knowledge
                |            |             |
                +------------+-------------+
                             |
                             v
                    Shared GGUF Runtime
                      (Qwen / other)
                             |
                             v
                          Answer
```

---

# Fresh Windows setup

## 1. Open the project

```powershell
cd D:\python\Models\orianbelt-v1.3
```

## 2. Check Python

```powershell
python --version
py --list
```

Use Python 3.12 for the current project.

If 3.12 is not installed and your Windows Python launcher supports installation:

```powershell
py install 3.12
```

Verify:

```powershell
py -3.12 --version
```

Expected:

```text
Python 3.12.x
```

## 3. Remove an old Python 3.14 virtual environment

If an old `.venv` is active:

```powershell
deactivate
```

Delete it:

```powershell
Remove-Item -Recurse -Force .venv
```

If `deactivate` is not recognized, close PowerShell, open a new window in the project directory, and remove `.venv`.

## 4. Create the Python 3.12 environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Verify **before installing packages**:

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

The version should be `Python 3.12.x` and the executable should point to:

```text
...\orianbelt-v1.3\.venv\Scripts\python.exe
```

## 5. Upgrade packaging tools

```powershell
python -m pip install --upgrade pip setuptools wheel
```

## 6. Install Windows CPU dependencies

Run the included installer:

```powershell
.\install-windows-cpu.ps1
```

If PowerShell blocks the script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install-windows-cpu.ps1
```

Verify:

```powershell
python -c "from llama_cpp import Llama; print('llama.cpp OK')"
```

Expected:

```text
llama.cpp OK
```

---

# GGUF model setup

The Orianbelt ZIP does not include a multi-GB GGUF model.

Create the model directory:

```powershell
New-Item -ItemType Directory -Force .\models\base
```

Put your GGUF there.

Example:

```text
orianbelt-v1.3\
+-- models\
|   +-- base\
|       +-- qwen2.5-1.5b-instruct-fp16.gguf
+-- orianbelt\
+-- data\
+-- README.md
```

Check the **real** filename:

```powershell
Get-ChildItem .\models\base\*.gguf
```

Always configure Orianbelt using the filename this command actually displays.

For an 8 GB CPU machine, a Q4 quantized GGUF will generally require less memory than FP16. The existing FP16 model can still be tested if it loads successfully.

---

# Create knowledge profiles

## Banking

```powershell
python -m orianbelt.cli create RBI_ACT `
  --gguf .\models\base\qwen2.5-1.5b-instruct-fp16.gguf `
  --domain banking `
  --description "RBI Act, banking, monetary policy and financial regulation"
```

## Programming

```powershell
python -m orianbelt.cli create PROGRAM `
  --gguf .\models\base\qwen2.5-1.5b-instruct-fp16.gguf `
  --domain programming `
  --description "Python, JavaScript, APIs, software development and debugging"
```

Both profiles can point to the same GGUF.

List profiles:

```powershell
python -m orianbelt.cli models
```

---

# Add your knowledge

Recommended structure:

```text
data\
+-- banking\
|   +-- RBI_ACT.pdf
|   +-- banking_rules.pdf
|   +-- notes.txt
|
+-- programming\
    +-- python_notes.pdf
    +-- api_guide.docx
```

Supported knowledge files:

```text
.txt
.pdf
.docx
```

Index banking:

```powershell
python -m orianbelt.cli learn RBI_ACT .\data\banking
```

Index programming:

```powershell
python -m orianbelt.cli learn PROGRAM .\data\programming
```

`learn` does **not** retrain Qwen. It extracts text, chunks documents, builds the local knowledge index, and associates that knowledge with the selected profile.

When documents change or new documents are added, run `learn` again.

---

# Automatic routing

Start Orianbelt itself:

```powershell
python -m orianbelt.cli run
```

Example:

```text
Orianbelt >>> What powers does RBI have?

[route: RBI_ACT]
Orianbelt >>> ...
```

Programming:

```text
Orianbelt >>> Write a Python REST API.

[route: PROGRAM]
Orianbelt >>> ...
```

Cross-domain:

```text
Orianbelt >>> Write Python code that implements the banking rule in our documents.

[route: RBI_ACT, PROGRAM]
Orianbelt >>> ...
```

Orianbelt can retrieve context from up to two selected profiles.

---

# Force a profile

Banking only:

```powershell
python -m orianbelt.cli run RBI_ACT
```

Programming only:

```powershell
python -m orianbelt.cli run PROGRAM
```

This is useful when an application already knows which domain should answer.

---

# Interactive commands

```text
/help
/route
/sources
/info
/clear
/exit
```

- `/route` - shows the selected profile(s).
- `/sources` - shows document evidence used for the previous answer.
- `/info` - shows runtime/profile information.
- `/clear` - clears conversation history.
- `/exit` - closes the CLI.

---

# What if information is not in the documents?

Normal v1.3 flow:

```text
Question
   |
   v
Route to relevant domain(s)
   |
   v
Search domain knowledge
   |
   +---- useful context found ---> context + question ---> GGUF
   |
   +---- no suitable domain route ----------------------> GENERAL
                                                              |
                                                              v
                                                   pretrained knowledge
```

If no configured domain passes the routing threshold, Orianbelt reports:

```text
GENERAL
```

and the base instruction model may answer from its pretrained knowledge.

For document-sensitive questions, check:

```text
/sources
```

If the source documents do not contain a fact, Orianbelt cannot reliably claim that the fact came from your documents. A future strict-document mode can enforce "answer only from loaded evidence."

---

# Local API

Start:

```powershell
python -m orianbelt.cli serve
```

Default local address:

```text
127.0.0.1:11435
```

Useful endpoints:

```text
GET  /health
GET  /v1/models
POST /v1/generate
```

Automatic routing JSON:

```json
{
  "model": "auto",
  "prompt": "What does the RBI Act say about this?"
}
```

Forced banking profile:

```json
{
  "model": "RBI_ACT",
  "prompt": "Explain Section 22 using the loaded documents."
}
```

Use `"model": "auto"` when Orianbelt should decide. Use a profile name when your application already knows the required domain.

---

# Troubleshooting / mismatches

## `ModuleNotFoundError: No module named 'llama_cpp'`

Check:

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

Make sure the v1.3 `.venv` is active and Python is 3.12.

Then:

```powershell
.\install-windows-cpu.ps1
```

Verify:

```powershell
python -c "from llama_cpp import Llama; print('llama.cpp OK')"
```

---

## `nmake` / CMake / compiler error

Typical error:

```text
Running 'nmake' '-?' failed
CMAKE_C_COMPILER not set
CMAKE_CXX_COMPILER not set
Failed building wheel for llama-cpp-python
```

This means pip tried to compile `llama-cpp-python` from source.

Check:

```powershell
python --version
```

If it is Python 3.14, rebuild `.venv` using Python 3.12.

Then use:

```powershell
.\install-windows-cpu.ps1
```

Do not install a full C++ compiler toolchain unless you intentionally want to compile llama.cpp yourself.

---

## `FileNotFoundError: GGUF model not found`

Find the actual file:

```powershell
Get-ChildItem .\models\base\*.gguf
```

Example mismatch:

```text
Configured:
qwen2.5-1.5b-instruct-q4_k_m.gguf

Actually installed:
qwen2.5-1.5b-instruct-fp16.gguf
```

Recreate/update the profile using the real filename:

```powershell
python -m orianbelt.cli create RBI_ACT `
  --gguf .\models\base\qwen2.5-1.5b-instruct-fp16.gguf `
  --domain banking `
  --description "RBI Act, banking, monetary policy and financial regulation"
```

Inspect the profile:

```powershell
Get-Content .\models\RBI_ACT\assistant.json
```

Confirm its `gguf` path exists.

---

## `.ggu` versus `.gguf`

Wrong:

```text
model.ggu
```

Normal GGUF extension:

```text
model.gguf
```

Do not guess. Run:

```powershell
Get-ChildItem .\models\base\
```

and copy the exact filename.

---

## Wrong Python environment is active

Run:

```powershell
python --version
where.exe python
```

The active executable should normally be:

```text
...\orianbelt-v1.3\.venv\Scripts\python.exe
```

If not:

```powershell
.\.venv\Scripts\Activate.ps1
```

---

## Banking question routes to PROGRAM

Inside chat:

```text
/route
```

Make profile descriptions clear.

Good banking description:

```text
RBI Act, Reserve Bank of India, banks, monetary policy, financial regulation
```

Good programming description:

```text
Python, JavaScript, APIs, source code, software engineering, debugging
```

If routing must not decide, force banking:

```powershell
python -m orianbelt.cli run RBI_ACT
```

v1.3 routing is intentionally lightweight for CPU use. A later version can replace it with a neural embedding/router provider.

---

## Correct route, but answer cannot find the document information

Check:

```text
/sources
```

Then verify:

1. The correct document is inside the learned directory.
2. It is `.txt`, `.pdf`, or `.docx`.
3. You ran `learn` after adding/changing it.
4. The document actually contains the information.
5. The question contains enough context to identify the topic.

Re-index:

```powershell
python -m orianbelt.cli learn RBI_ACT .\data\banking
```

Then test the forced profile:

```powershell
python -m orianbelt.cli run RBI_ACT
```

---

## Added a document but Orianbelt does not know it

Copying a file alone does not update the index.

Run:

```powershell
python -m orianbelt.cli learn RBI_ACT .\data\banking
```

again.

---

## FP16 is too slow or uses too much RAM

Check:

```powershell
Get-ChildItem .\models\base\*.gguf
```

An FP16 model is significantly larger than a Q4 quantized version of the same model.

On an 8 GB CPU laptop, use an appropriate Q4 GGUF if FP16 is too slow or causes memory pressure, then point your profiles to the replacement file.

You do not need to duplicate the GGUF for every profile.

---

# Recommended v1.3 test

Start with:

```text
RBI_ACT -> banking
PROGRAM -> programming
```

Test:

```text
What powers does RBI have?

Write a Python function that validates an email address.

Explain the banking rule in our document and show a Python implementation.

What is the capital of Japan?
```

After tests, inspect:

```text
/route
/sources
```

This helps distinguish a routing problem from a retrieval problem or a base-model answer-quality problem.

---

# Knowledge vs training

v1.3 uses:

```text
Documents -> RAG / knowledge index
```

This is appropriate for domain facts, private documents, laws, manuals, and information that may change.

Future versions can add:

```text
Training examples -> LoRA / QLoRA
```

LoRA should primarily teach behavior, terminology, output format, or specialized tasks rather than replace RAG for frequently changing factual documents.

Future architecture:

```text
Base model
    +
Domain LoRA
    +
Domain RAG
    +
Conversation
    =
Orianbelt response
```

---

# Current limitations

Orianbelt v1.3 is a development prototype.

- Routing/retrieval is lightweight and local, not yet a large neural embedding system.
- No LoRA/QLoRA training pipeline yet.
- No distributed GPU/cloud worker system yet.
- No production authentication or tenant isolation yet.
- Document extraction depends on source document quality.
- The base language model can still make mistakes.
- RAG improves grounding but does not guarantee correctness.
- GGUF size/model quality strongly affects reasoning, language quality, memory usage, and speed.

---

# Roadmap

```text
v1.3
Automatic domain routing
Multi-domain RAG
Sources
Shared GGUF runtime
        |
        v
v1.4
Provider abstraction
Streaming
Better sessions/memory
Evaluation tools
Strict knowledge modes
        |
        v
v1.5
LoRA / QLoRA jobs
Adapter registry
Adapter routing
Evaluation + rollback
        |
        v
v2.x
GPU workers
Local/cloud hybrid routing
Scalable vector database
Authentication
Multi-user / multi-tenant runtime
Observability
Load balancing
```

The base model should remain replaceable. Orianbelt owns the knowledge, routing, profiles, API, sessions, and eventually adapters, training, and distributed inference.
