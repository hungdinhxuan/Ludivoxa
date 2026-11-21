param(
    [string]$Python = '.venv/Scripts/python.exe',
    [string]$ModelDirectory = 'models'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
New-Item -ItemType Directory -Force build | Out-Null
$previousConfig = '-'
if (Test-Path -LiteralPath 'dist/Interpreter/config.yml') {
    $previousConfig = Join-Path $repoRoot 'build/user-config-before.yml'
    Copy-Item -LiteralPath 'dist/Interpreter/config.yml' -Destination $previousConfig -Force
}
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$buildProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m', 'PyInstaller', '--noconfirm', 'packaging/windows.spec' -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $repoRoot 'build/pyinstaller.stdout.log') -RedirectStandardError (Join-Path $repoRoot 'build/pyinstaller.log')
if ($buildProcess.ExitCode -ne 0) { throw 'PyInstaller build failed; see build/pyinstaller.log.' }
$modelRoot = (Resolve-Path -LiteralPath $ModelDirectory).Path
@'
import shutil
import sys
from pathlib import Path
import yaml
from interpreter.config import Config, LLMSettings, OCRBackend, OverlayMode, PaddleSettings, TranslationBackend
from interpreter.paddle_ocr import select_paddle_recognizer

source = Path(sys.argv[1])
target = Path('dist/Interpreter')
for model in ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec'):
    shutil.copytree(source / model, target / 'models' / model, dirs_exist_ok=True)
english = source / 'en_PP-OCRv5_mobile_rec'
if english.is_dir():
    shutil.copytree(english, target / 'models' / english.name, dirs_exist_ok=True)
korean = source / 'korean_PP-OCRv5_mobile_rec'
korean_target = target / '_internal' / 'ocr-models' / korean.name
if korean.is_dir():
    shutil.copytree(korean, korean_target, dirs_exist_ok=True)
for name in ('PP-OCRv5_mobile_det', 'en_PP-OCRv5_mobile_rec', 'PP-OCRv5_mobile_rec', korean.name):
    exported = source / (name + '_onnx')
    if exported.is_dir():
        original = korean_target if name == korean.name else target / 'models' / name
        if not original.is_dir():
            shutil.copytree(exported, original)
            continue
        current = yaml.safe_load((original / 'inference.yml').read_text(encoding='utf-8'))
        metadata = yaml.safe_load((exported / 'inference.yml').read_text(encoding='utf-8'))
        if any(current.get(key) != metadata.get(key) for key in ('Global', 'PreProcess', 'PostProcess')):
            raise ValueError(f'ONNX export metadata does not match the existing {name} model.')
        shutil.copy2(exported / 'inference.onnx', original / 'inference.onnx')
        for src, dest in (('source.json', 'onnx-source.json'), ('README.md', 'ONNX_MODEL_CARD.md')):
            if (exported / src).is_file():
                shutil.copy2(exported / src, original / dest)
select_paddle_recognizer(PaddleSettings(str(target / 'models/PP-OCRv5_mobile_det'), str(korean_target)), 'Korean')
nano = source / 'VieNeu-TTS-v3-Nano'
if nano.is_dir():
    shutil.copytree(nano, target / 'models' / nano.name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('*.part', '.cache'))
voice_dir = source / 'voices' if (source / 'voices').is_dir() else source / 'voice'
voice_target = target / 'models' / 'voices'
voice_target.mkdir(parents=True, exist_ok=True)
for model in voice_dir.glob('vi_*.onnx'):
    shutil.copy2(model, voice_target / model.name)
    shutil.copy2(Path(str(model) + '.json'), voice_target / (model.name + '.json'))
for card in voice_dir.glob('*.MODEL_CARD'):
    shutil.copy2(card, voice_target / card.name)
config = Config.load(sys.argv[2]) if sys.argv[2] != '-' else Config(
    source_language='Auto', ocr_backend=OCRBackend.PADDLE, translation_backend=TranslationBackend.LLM,
    overlay_mode=OverlayMode.BANNER,
    paddle=PaddleSettings('models/PP-OCRv5_mobile_det', 'models/PP-OCRv5_mobile_rec'),
    llm=LLMSettings(model='qwen3-4b-instruct-2507:q4_k_m', request_options={'options': {'num_ctx': 2048}}),
    tts_enabled=True, tts_model_path='models/voices/vi_VN-vais1000-medium.onnx',
)
config.save(str(target / 'config.yml'))
shutil.copy2('LICENSE', target / 'LICENSE')
'@ | & $Python - $modelRoot $previousConfig
if ($LASTEXITCODE -ne 0) { throw 'Copying local models/config failed.' }
Write-Host 'Built dist/Interpreter/Interpreter.exe. Keep the complete Interpreter folder together.'
