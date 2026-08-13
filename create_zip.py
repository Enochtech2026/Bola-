import shutil
import os

ROOT = os.path.abspath(os.path.dirname(__file__))
TARGET = os.path.join(os.path.dirname(ROOT), 'E-library-submission')
ZIP_PATH = os.path.join(os.path.dirname(ROOT), 'E-library-submission')

def make_zip():
    base_name = ZIP_PATH
    if os.path.exists(base_name + '.zip'):
        os.remove(base_name + '.zip')
    shutil.make_archive(base_name, 'zip', ROOT)
    print('Created:', base_name + '.zip')

if __name__ == '__main__':
    make_zip()
