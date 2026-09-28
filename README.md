<<<<<<< HEAD
# Orianbelt v1.2
Local-first GGUF + document retrieval prototype.

## Windows setup
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If llama-cpp-python needs the CPU wheel:
```powershell
python -m pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Place a GGUF instruct model in `models\base`, then:
```powershell
python -m orianbelt.cli create RBI_ACT --gguf models\base\qwen2.5-1.5b-instruct-q4_k_m.gguf
python -m orianbelt.cli learn RBI_ACT .\data\training
python -m orianbelt.cli run RBI_ACT
```

Inside chat use `/help`, `/sources`, `/clear`, `/info`, `/exit`.

`learn` indexes TXT/PDF/DOCX. It does not retrain the base model.
=======
# OrainBelt
For Simple handy local supprot system for machine
>>>>>>> 36b5a6d34345f4200083f161d7e7323ee467091b
