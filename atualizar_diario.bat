@echo off
rem Tarefa diaria (Agendador do Windows): roda o pipeline so se a ANEEL publicou versao nova
cd /d "%~dp0scripts"
set PYTHONIOENCODING=utf-8
echo ==== %date% %time% >> "..\dados\raw\atualizacao_diaria.log"
"C:\Users\mbarreto\AppData\Local\Python\pythoncore-3.14-64\python.exe" atualizar_tudo.py --so-se-novo >> "..\dados\raw\atualizacao_diaria.log" 2>&1
