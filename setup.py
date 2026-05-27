from setuptools import setup, find_packages

setup(
    name="goblintools",
    version="0.7.8",
    packages=find_packages(),
    install_requires=[
        "patool",
        "rarfile",
        "boto3",
        "opencv-python-headless",
        "numpy",
        "pdf2image",
        "pypdf>=6.10.2",
        "cryptography>=3.1",
        "beautifulsoup4",
        "striprtf",
        "dbfread",
        "python-docx",
        "python-pptx",
        "openpyxl",
        "xlrd",
        "odfpy",
        "unidecode",
        "pytesseract",
        "scipy",
        "pillow"
    ],
    author="Gean Matos",
    author_email="gean@webgoal.com.br",
    description="Toolkit for archive extraction, OCR parsing, and file text extraction",
    license="MIT",
    include_package_data=True,
)
