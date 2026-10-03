"""Build the Windows release of BiteMap Logger.

    python tools/build_client.py

1. vendor Tesseract into client/vendor/tesseract (only tesseract.exe + the DLLs it imports) + tessdata_fast eng/deu/rus
   Tesseract is taken from $TESSERACT_DIR or the default install folder (choco install tesseract / UB-Mannheim build).
2. PyInstaller (one-folder build, windowed)
3. zip -> dist/BiteMapLogger-<version>-win64.zip
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, 'client')
VENDOR = os.path.join(CLIENT, 'vendor', 'tesseract')
TESSDATA_URL = 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata'
LANGS = ('eng', 'deu', 'rus')


def vendor_tesseract():
    exe = os.path.join(VENDOR, 'tesseract.exe')
    if not os.path.exists(exe):
        src = os.environ.get('TESSERACT_DIR') or r'C:\Program Files\Tesseract-OCR'
        if not os.path.exists(os.path.join(src, 'tesseract.exe')):
            sys.exit(f'Tesseract not found in {src}. Install it (choco install tesseract) or set TESSERACT_DIR.')
        os.makedirs(VENDOR, exist_ok=True)
        shutil.copy2(os.path.join(src, 'tesseract.exe'), exe)
        for f in os.listdir(src):
            if f.lower().endswith('.dll'):
                shutil.copy2(os.path.join(src, f), os.path.join(VENDOR, f))
        prune_dlls()
        for f in os.listdir(VENDOR):
            if f.lower().endswith('.dll') and os.path.getsize(os.path.join(VENDOR, f)) > 20_000_000:
                print('stripping debug info from', f, strip_debug(os.path.join(VENDOR, f)))
    os.makedirs(os.path.join(VENDOR, 'tessdata'), exist_ok=True)
    for lang in LANGS:
        dst = os.path.join(VENDOR, 'tessdata', f'{lang}.traineddata')
        if not os.path.exists(dst):
            print('downloading', lang)
            urllib.request.urlretrieve(TESSDATA_URL.format(lang), dst)
    lic = os.path.join(VENDOR, 'LICENSE-tesseract.txt')
    if not os.path.exists(lic):
        with open(lic, 'w', encoding='utf-8') as f:
            f.write('Tesseract OCR - Apache License 2.0 - https://github.com/tesseract-ocr/tesseract\n'
                    'Language data: tessdata_fast - Apache License 2.0 - https://github.com/tesseract-ocr/tessdata_fast\n')


def prune_dlls():
    """Keep only DLLs reachable from tesseract.exe's import table."""
    import pefile
    have = {f.lower(): f for f in os.listdir(VENDOR) if f.lower().endswith('.dll')}
    need, todo = set(), ['tesseract.exe']
    while todo:
        pe = pefile.PE(os.path.join(VENDOR, todo.pop()), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
                                               pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
        for attr in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT'):
            for imp in getattr(pe, attr, []):
                n = imp.dll.decode().lower()
                if n in have and n not in need:
                    need.add(n)
                    todo.append(have[n])
    for n in set(have) - need:
        os.remove(os.path.join(VENDOR, have[n]))


def strip_debug(path):
    """Remove trailing DWARF debug sections (MinGW builds ship ~95 MB of them in libtesseract)."""
    import pefile
    pe = pefile.PE(path)
    names = [sec.Name.rstrip(b'\x00').decode(errors='replace') for sec in pe.sections]
    keep = len(names)
    while keep and names[keep - 1].startswith(('/', '.debug')):
        keep -= 1
    if keep == len(names) or any(n.startswith(('/', '.debug')) for n in names[:keep]):
        pe.close()
        return False
    last = pe.sections[keep - 1]
    align = pe.OPTIONAL_HEADER.SectionAlignment
    new_size_img = -(-(last.VirtualAddress + max(last.Misc_VirtualSize, last.SizeOfRawData)) // align) * align
    sec_dir = pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_SECURITY']
    for i, dd in enumerate(pe.OPTIONAL_HEADER.DATA_DIRECTORY):  # nothing we keep may point into the removed part
        if i == sec_dir:
            # Authenticode signature (a file offset, at the very end); it covers the debug data, so it has to go
            dd.VirtualAddress = dd.Size = 0
        elif dd.Size and dd.VirtualAddress + dd.Size > new_size_img:
            pe.close()
            return False
    end = last.PointerToRawData + last.SizeOfRawData
    pe.FILE_HEADER.NumberOfSections = keep
    pe.FILE_HEADER.PointerToSymbolTable = 0
    pe.FILE_HEADER.NumberOfSymbols = 0
    pe.OPTIONAL_HEADER.SizeOfImage = new_size_img
    for sec in pe.sections[keep:]:  # wipe the removed section headers
        sec.Name = b'\x00' * 8
        for attr in ('Misc_VirtualSize', 'VirtualAddress', 'SizeOfRawData', 'PointerToRawData',
                     'PointerToRelocations', 'PointerToLinenumbers', 'NumberOfRelocations',
                     'NumberOfLinenumbers', 'Characteristics'):
            setattr(sec, attr, 0)
    data = pe.write()[:end]
    pe.close()
    with open(path, 'wb') as f:
        f.write(data)
    return True


def version():
    src = open(os.path.join(CLIENT, 'bitemap_logger', '__init__.py'), encoding='utf-8').read()
    return re.search(r"__version__ = '([^']+)'", src).group(1)


def main():
    vendor_tesseract()
    subprocess.check_call([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                           '--distpath', os.path.join(ROOT, 'dist'), '--workpath', os.path.join(ROOT, 'build'),
                           os.path.join(CLIENT, 'bitemap_logger.spec')])
    out = os.path.join(ROOT, 'dist', f'BiteMapLogger-{version()}-win64')
    shutil.make_archive(out, 'zip', os.path.join(ROOT, 'dist'), 'BiteMapLogger')
    print('built', out + '.zip')


if __name__ == '__main__':
    main()
