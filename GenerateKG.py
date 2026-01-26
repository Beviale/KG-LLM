from dotenv import load_dotenv
import json
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.models.litellm import LiteModel
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from graphrag_sdk.source import TEXT
from pypdf import PdfReader
import unicodedata
import re

load_dotenv()

class DataItem:
    """
    Reperesents a PDF document to process
    """
    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self._text = None
        self._text_path = None
        self._num_pages = None

    
    @property
    def text(self):
        if self._text == None:
            self._text, self._num_pages = retrieve_text_from_pdf(self.pdf_path)
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

         
    @property
    def num_pages(self):
        if self._num_pages == None:
            self._text, self._num_pages = retrieve_text_from_pdf(self.pdf_path)
        return self._num_pages

    @num_pages.setter
    def num_pages(self, value):
        self._num_pages = value

        

    @property
    def text_path(self):
        if self._text_path == None:
           self._text_path = save_text(self.pdf_path, self.text)
        return self._text_path

    @text_path.setter
    def text_path(self, value):
       self._text_path = value

    


def preprocess(text: str):
    # 1. We transform all the characters in lowercase 
    new_text = text.lower()
    new_text = re.sub(r'\b([A-Z]{2,})\b', lambda m: m.group(1).upper(), text)

    # 2. We normalize unicode (accents, apostrophes, etc.)
    new_text = unicodedata.normalize("NFKC", new_text) 
    # 3. We remove the special characters
    new_text = ''.join(c for c in new_text if c.isprintable() or c in '\n\t')
    new_text = re.sub(r'\s+([.,;:!?])', r'\1', new_text)
    new_text = re.sub(r'(\w+)\s*-\s*(\w+)', r'\1-\2', new_text)

    return new_text



def retrieve_text_from_pdf(pdf_path: str):
    """
        Give a pdf document path, it retrieves the text inside it. 
    """
    num_pages = 0
    pdf_text = ""
    reader = PdfReader(pdf_path)
    for page in reader.pages:
        num_pages = num_pages + 1
        pdf_text = pdf_text + page.extract_text()
    pdf_text = preprocess(pdf_text)
    return pdf_text, num_pages


def save_text(pdf_path: str, pdf_text: str):
    pdf_path_split = pdf_path.split("\\")
    pdf_name = pdf_path_split[len(pdf_path_split) - 1].removesuffix(".pdf")
    pdf_text_path = r"InputPDFtoText/" + pdf_name + ".txt"
    with open(pdf_text_path, "w", encoding="utf-8") as f:
        f.write(pdf_text)
    return pdf_text_path



# Import Data
pdf_paths = [r"InputPDF\Disciplina di utilizzo\DISCIPLINA_Utilizzo_EmPULIA_ver_1 7.pdf"]
dataItems = []
for pdf_path in pdf_paths:
    dataItem = DataItem(pdf_path)
    path = dataItem.text_path
    dataItems.append(dataItem)

all_text_paths = [item.text_path for item in dataItems]

sources = [TEXT(text_path) for text_path in all_text_paths]

model = LiteModel(model_name="openai/gpt-4.1-nano")

# Ontology Auto-Detection
ontology = Ontology.from_sources(
    sources=sources,
    model=model,
)
# Save the ontology to the disk as a json file.
with open("ontology.json", "w", encoding="utf-8") as file:
    file.write(json.dumps(ontology.to_json(), indent=2))