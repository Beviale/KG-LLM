from dotenv import load_dotenv
import json
import Utils
from graphrag_sdk import KnowledgeGraph, Ontology
from graphrag_sdk.helpers import extract_json, map_dict_to_cypher_properties
import litellm
from litellm import completion, ContextWindowExceededError
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from graphrag_sdk.source import TEXT
from pypdf import PdfReader
import unicodedata
import re
from pathlib import Path
from graphrag_sdk.models.litellm import LiteModel
import Prompt
import os
from falkordb import FalkorDB
from graphrag_sdk.steps import extract_data_step
import pysbd
from colorama import init, Fore, Style
import numpy as np
from tqdm import tqdm
from litellm import embedding
import umap
from sklearn.cluster import HDBSCAN
from sklearn.preprocessing import StandardScaler
import copy
import traceback


init(autoreset=True)

LIMIT_WHILE_LLM = 10 # Indicates the maximum number of times the script can repeat the same question to the LLM.


load_dotenv()

host = os.getenv("FALKORDB_HOST", "localhost")
port = int(os.getenv("FALKORDB_PORT", "6379"))
username = os.getenv("FALKORDB_USERNAME", "beviale")  
password = os.getenv("FALKORDB_PASSWORD", "12345678Ab!") 

client_kwargs = {
    "host": host,
    "port": port,
}



class DataItem:
    """
    Reperesents a PDF document to process
    """
    def __init__(self, category, pdf_path):
        self.pdf_path = pdf_path
        self.category = category
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
           self._text_path = save_text(self.category, str(self.pdf_path), self.text)
        return self._text_path

    @text_path.setter
    def text_path(self, value):
       self._text_path = value

    
def selective_lower(match):
    """
    A function that, given a word match, returns the same word if it is an acronym (like 'UE'); otherwise, it returns the word in lowercase.    
    """
    word = match.group(0)
    exclude_pattern = re.compile(r'[A-Z\']+')
    if exclude_pattern.fullmatch(word):
        return word
    return word.lower()


def preprocess(text: str):
    """
    It processes the given text extracted from a PDF file, cleaning out unwanted characters and minimizing the number of tokens required for the LLM API call.       
    """
    new_text = text
    # 1. We normalize unicode (accents, apostrophes, etc.)
    new_text = unicodedata.normalize("NFKC", new_text) 
    # 2. We remove the special characters
    new_text = ''.join(c for c in new_text if c.isprintable() or c in '\n\t')
    # 3. From "prova :" to-> "prova:"
    new_text = re.sub(r'\s+([.,;:!?])', r'\1', new_text)
    # 4. From "prova1 - prova2" to-> "prova1-prova2"
    new_text = re.sub(r'([A-Za-zÀ-ÿ]+)\s*[-]\s*([A-Za-zÀ-ÿ]+)', r'\1-\2', new_text)
    # 5. We remove the empty rows
    pattern_emty_rows = r'(?:^\s*\r?\n){3,}'
    new_text = re.sub(pattern_emty_rows, r'\r\n', new_text, flags=re.MULTILINE)
    # 6. We transform all the text in lowercase excpet for the acronyms
    word_pattern = re.compile(r'\b[\w]+\b')
    new_text =  word_pattern.sub(selective_lower, new_text)
    new_text = re.sub(r'[ \t]+', ' ', new_text)
    return new_text

   

def retrieve_text_from_pdf(pdf_path: str):
    """
    Given a PDF document path, it retrieves the text inside it. 
    """
    num_pages = 0
    pdf_text = ""
    reader = PdfReader(pdf_path)
    for page in reader.pages:
        num_pages = num_pages + 1
        pdf_text = pdf_text + page.extract_text()
    pdf_text = preprocess(pdf_text)
    return pdf_text, num_pages


def save_text(category: str, pdf_path: str, pdf_text: str):
    """
    Given a text extracted from a PDF file, it creates the corresponding .txt file.
    """
    pdf_path_split = pdf_path.split("\\")
    pdf_name = pdf_path_split[len(pdf_path_split) - 1].removesuffix(".pdf")
    pdf_text_path = r"InputPDFtoText/" + category + r"/" + pdf_name + ".txt"
    with open(pdf_text_path, "w", encoding="utf-8") as f:
        f.write(pdf_text)
    return pdf_text_path



def preprocess_pdf(pdf_dict):
    """
    Given a pdf_dict, it creates the corresponding .txt files.
    
    :param pdf_dict:: a dictionary where each key is a category/topic (e.g., "DisciplinaDiUtilizzo") and each value is the list of PDF file paths associated with it.
    """
    dataItems = []
    for category, pdf_paths in pdf_dict.items():
        for pdf_path in pdf_paths:
            dataItem = DataItem(category, pdf_path)
            path = dataItem.text_path
            dataItems.append(dataItem)
            print(f"PDF file \"{pdf_path}\" processed correctly.")
    return dataItems


def process_response_ontology(category, index_chunk, response, text, model):
    """
    It processes the LLM response to create the ontology for a specific category (e.g., 'DisciplinaDiUtilizzo').
    Returns 'True' if the ontology has been processed and saved correctly; 'False' otherwise.
    """
    response_content = response.choices[0].message["content"].strip()  
    limit_while_count = 0
    while(True):
        limit_while_count = limit_while_count + 1
        if limit_while_count>LIMIT_WHILE_LLM:
            print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
            return False
        try:
            data = Utils.validate_generated_ontology(response_content) 
            print(f"{Fore.WHITE}Chunk ontology created!")
            break
        except Exception as e:        
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. TypeError: {error}")
                print(f"Prompting model to fix JSON")
                if isinstance(e, Utils.JSONFormattingException):
                    json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ONTOLOGY_ITA.format(error=error, json=response_content, text=text)}         
                        ]
                    )
                else:
                        json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_ITA.format(ontology=response_content, errors=error, text=text)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                print(f"{Fore.RED}Exception throws: {str(e)}")
                continue       

    
    current_ontology_ident = json.dumps(data, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology '{category}' with index chunk:'{index_chunk}' created successfully!")
        ontology_file_name = f"Ontologies/{category}/{index_chunk}_Ontology.json"
        # Save the ontology to the disk as a json file.
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)
    return True



def split_text_chunks(text: str, ontology=False):
    """
    It splits the given text into text chunks considering the given maximum number of characters. 
    It returns the list of text chunks. 
    """
    if ontology==True:
        max_characters=20000
        back_characters = 3000
    else:
        max_characters=10000
        back_characters = 1500



    seg = pysbd.Segmenter(language="it", clean=True)
    sentences = seg.segment(text)

    chunks = []
    chunk = []
    while(True):
        if not sentences:
            break
        maximum_reached = False
        for sentence in sentences:
            chunk.append(sentence)
            number_of_charachters = sum(len(s) for s in chunk)
            if (number_of_charachters>max_characters): 
                maximum_reached = True             
                break
            
        if maximum_reached:
            chunk_reverse = chunk.copy()
            chunk_reverse.reverse()
            number_of_characters = 0
            index = 0
            for chunk_rev in chunk_reverse:
                index = index + 1
                number_of_characters = number_of_characters + len(chunk_rev)
                if (number_of_characters>back_characters):
                    break
            sentences = sentences[len(chunk)-index:]
           
        else:
            sentences = [] 
        chunks.append(" ".join(chunk))
        chunk.clear()
    return chunks



def get_step_ontologies(category):
    json_merge = []
    next_level_index = 0
    while(True):
        next_level_index = next_level_index + 1
        if os.path.exists(f"Ontologies/{category}/NextLevel_{next_level_index}") == False:
            break

    numberOfIterationsToSkip = 0
    if next_level_index > 1:
        last_next_level_dir = Path(f"Ontologies/{category}/NextLevel_{next_level_index-1}")       
        last_last_next_level_dir = Path(f"Ontologies/{category}/NextLevel_{next_level_index-2}")       
        if os.path.exists(f"Ontologies/{category}/NextLevel_{next_level_index-1}/final.txt") == False:
            next_level_index = next_level_index - 1
            if os.path.exists(last_last_next_level_dir):
                for file_path in last_last_next_level_dir.iterdir():
                    if file_path.suffix.lower() != ".json":
                        continue
                    with file_path.open("r", encoding="utf-8") as f:
                        json_item = json.load(f)
                    json_merge.append(json_item)
            else:
                index = 1
                while(True):
                    file_path = Path(f"Ontologies/{category}/{index}_Ontology.json")
                    if file_path.exists()==False:
                        break
                    
                    with file_path.open("r", encoding="utf-8") as f:                       
                        json_item = json.load(f)

                    json_merge.append(json_item)
                    index += 1

            for file_path in last_next_level_dir.iterdir():
                numberOfIterationsToSkip = numberOfIterationsToSkip + 1
        else:
            for file_path in last_next_level_dir.iterdir():
                if file_path.suffix.lower() != ".json":
                    continue
                with file_path.open("r", encoding="utf-8") as f:
                    json_item = json.load(f)
                json_merge.append(json_item)
    else:
        index = 1
        while(True):
            file_path = Path(f"Ontologies/{category}/{index}_Ontology.json")
            if file_path.exists()==False:
                break
            
            with file_path.open("r", encoding="utf-8") as f:
                json_item = json.load(f)

            json_merge.append(json_item)
            index += 1
    return next_level_index, numberOfIterationsToSkip, json_merge


def merge_ontologies_chunk(category, model=None):
    """
    It merges the ontologies created for each text chunk of a specific category (e.g. 'DisciplinaDiUtilizzo') into a single ontology file.
    Returns 'True' if the chunk ontologies have been merged and saved correctly; 'False' otherwise.
    """
    if model is None:
        model = "openai/gpt-5-mini"

    next_level_index, numberOfIterationsToSkip, json_merge = get_step_ontologies(category)
    current_json_elements = json_merge[:]
    while len(current_json_elements) > 1:
        next_level = []
        dir_path_next_level = Path(f"Ontologies/{category}/NextLevel_{next_level_index}")
        dir_path_next_level.mkdir(parents=True, exist_ok=True) 
        index_merge_process = 0
        for i in range(0, len(current_json_elements), 2):
            index_merge_process = index_merge_process + 1
            if numberOfIterationsToSkip >= index_merge_process:
                continue
            numberOfIterationsToSkip = 0
            if i + 1 < len(current_json_elements):
                first_ontology = json.dumps(current_json_elements[i], ensure_ascii=False)
                second_ontology = json.dumps(current_json_elements[i+1], ensure_ascii=False)
                
                print("Waiting the LLM response to merge the ontologies...")
                response = completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                        {"role": "user",   "content": Prompt.MERGE_ONTOLOGY_PROMPT_ITA.format(first_ontology=first_ontology, second_ontology=second_ontology)}
                    ]
                ) 
                response_content = response.choices[0].message["content"].strip()  
                limit_while_count = 0
                while(True):
                    limit_while_count = limit_while_count + 1
                    if limit_while_count>LIMIT_WHILE_LLM:
                        print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
                        return False
                    try:
                        data = Utils.validate_generated_ontology(response_content) 
                        new_json = data
                        print("Chunk ontologies merged correctly!")
                        break
                    except Exception as e:                      
                        try:
                            # fallback
                            error = f"TypeError: '{type(e)}', error: '{e}'"
                            print(f"Error extracting JSON. {error}")
                            print(f"Prompting model to fix JSON")
                            if isinstance(e, Utils.JSONFormattingException):
                                json_fix_response = completion(
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                                        {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_ONTOLOGY_MERGE_ITA.format(errors=error, json=response_content, first_ontology=first_ontology, second_ontology=second_ontology)}         
                                    ]
                                )
                            else:
                                    json_fix_response = completion(
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": Prompt.MERGE_ONTOLOGY_SYSTEM_ITA},
                                        {"role": "user",   "content": Prompt.FIX_ONTOLOGY_PROMPT_MERGE_ITA.format(ontology=response_content, errors=error, first_ontology=first_ontology, second_ontology=second_ontology)}         
                                    ]
                                )

                            response_content = json_fix_response.choices[0].message["content"].strip()
                        except Exception as e:
                            print(f"{Fore.RED}Exception throws: {str(e)}")
                            continue
                next_level.append(new_json)                
            else:
                next_level.append(current_json_elements[i])
                
            next_level_file_index = 0
            while(True):
                next_level_file_index = next_level_file_index + 1
                if os.path.exists(f"Ontologies/{category}/NextLevel_{next_level_index}/{next_level_file_index}_Ontology.json") == False:
                    break
            json_item = next_level[-1]
            file_path_next_level = Path(f"Ontologies/{category}/NextLevel_{next_level_index}/{next_level_file_index}_Ontology.json")
            with open(file_path_next_level, "w", encoding="utf-8") as f:
                f.write(json.dumps(json_item, indent=2, ensure_ascii=False))
             
        end_next_level_file = Path(f"Ontologies/{category}/NextLevel_{next_level_index}/final.txt")
        with open(end_next_level_file, "w", encoding="utf-8") as f:
            f.write("END")
        next_level_index, numberOfIterationsToSkip, json_merge = get_step_ontologies(category)
        current_json_elements = json_merge[:] 


    new_json = current_json_elements[0]

    new_attr = {
        "name": "snippet",
        "type": "string",
        "unique": False,
        "required": True
    }

    for entity in new_json.get("entities", []): 
        attrs = entity.get("attributes")     
        if attrs is None:
            attrs = []
            entity["attributes"] = attrs
        
        exists = any(a.get("name") == "snippet" for a in attrs)
        if not exists:
            attrs.append(new_attr.copy())

    for relation in new_json.get("relations", []): 
        attrs = relation.get("attributes")     
        if attrs is None:
            attrs = []
            relation["attributes"] = attrs
        
        exists = any(a.get("name") == "snippet" for a in attrs)
        if not exists:
            attrs.append(new_attr.copy())


    current_ontology_ident = json.dumps(new_json, indent=2, ensure_ascii=False)
    if current_ontology_ident is not None:
        print(f"Ontology for the '{category}' category created successfully!")
        ontology_file_name = f"Ontologies/{category}/Ontology.json"
        with open(ontology_file_name, "w", encoding="utf-8") as file:
            file.write(current_ontology_ident)
    else:
        return False

    i = 0
    while(True):
        i = i + 1
        file_path = Path(f"Ontologies/{category}/{i}_Ontology.json")    
        if os.path.exists(file_path):
            os.remove(file_path)
        else:
            break
    return True

        

def split_codice_appalti(file_path):
    """
    It splits the given text (of the "CodiceAppalti" category) into text chunks considering the "Articoli". 
    It returns the list of text chunks. 
    """
    pattern = re.compile(r'^Art\.\s*\d+(-[\w]+)?\.\s*\(.*\)$', flags=re.UNICODE)
    chunks = []
    chunk = ""
    with file_path.open('r', encoding='utf-8', errors='replace') as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.rstrip('\r\n')
            line_nfc = unicodedata.normalize('NFC', line)
            
            if pattern.match(line_nfc):
                if len(chunk) > 200:
                    chunks.append(chunk)
                chunk = ""
            chunk = chunk + "\n" + line_nfc
    return chunks


def generate_ontology(category, model=None, dataItems=None):
    """
    It generates the ontology for the given category (e.g. 'DiscplinaDiUtilizzo')
    """    

    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-nano"

    ontology_file = Path(f"Ontologies/{category}/Ontology.json")
    if ontology_file.exists():
        print(f"{Fore.WHITE}The ontology '{ontology_file}' already exists!")
        return
    print(f"{Fore.GREEN}--Generating the ontology for the '{category}' category")
    chunks = []
    for text_path in all_text_paths:
        if os.path.exists(text_path)==False:
            print(f"{Fore.RED} The .txt file '{text_path}' does not exist!")
            continue

        if category == 'CodiceAppalti':
            chunks = split_codice_appalti(text_path)
            break
        
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        chunks.extend(split_text_chunks(text, True))
       

    index_chunk = 0
    for chunk in chunks:
        index_chunk = index_chunk + 1
        chunk_path = Path(f"Ontologies/{category}/{index_chunk}_Ontology.json")
        if chunk_path.exists():
            print(f"{Fore.WHITE}The chunk '{chunk_path}' has already been processed!")
            continue

        print(f"--{Fore.WHITE}Processing chunk {index_chunk}/{len(chunks)}.")
        textsToProcess = []
        textsToAdd = []
        textsToProcess.append(chunk)
        while(True):             
            for text in textsToProcess: 
                print(f"{Fore.WHITE}Waiting the LLM response...")
                try:     
                    response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.CREATE_ONTOLOGY_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.CREATE_ONTOLOGY_PROMPT_ITA.format(text=text)}
                        ]
                    )                  
                    if process_response_ontology(category, index_chunk, response, text, model)==False:
                        return 
                except ContextWindowExceededError as e:
                    mid = len(text) // 2
                    print(f"{Fore.WHITE}Halving the text size from '{len(text)}' to '{mid}'")
                    part1 = text[:mid]
                    part2 = text[mid:]
                    textsToAdd.append(part1)
                    textsToAdd.append(part2)
                    continue

            if textsToAdd:
                textsToProcess.clear()
                for textToAdd in textsToAdd:
                    textsToProcess.append(textToAdd)
                textsToAdd.clear()
            else:
                break
    merge_ontologies_chunk(category)
    print(f"{Fore.GREEN}Ontology created for the '{category}' category!")



def process_reponse_data(category, index_chunk, response, json_ontolgogy, text_ontology, ontology, text, model):
    """
    It processes the LLM response generated by the data-extraction prompt. It returns the JSON object containing entities, relations and attributes.
    """
    response_content = response.choices[0].message["content"].strip()
    #response_content = unicodedata.normalize('NFD', response_content)
    #response_content =  ''.join(ch for ch in response_content if unicodedata.category(ch) != 'Mn')
    limit_while_count = 0
    while(True):
        limit_while_count = limit_while_count + 1
        if limit_while_count>LIMIT_WHILE_LLM:
            print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
            return None
        try:
            data = Utils.get_json_data(response_content, ontology, json_ontolgogy)
            break
        except Exception as e:
            try:
                # fallback 
                print("--- DEBUG ERRORE ---")
                traceback.print_exc() 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. {error}")
                print(f"Prompting model to fix JSON")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_DATA_ITA.format(errors=error, json=response_content, text=text, ontology=text_ontology)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
                #response_content = unicodedata.normalize('NFD',  response_content)
                #response_content =  ''.join(ch for ch in  response_content if unicodedata.category(ch) != 'Mn')
            except Exception as e:
                continue

    
    id_chunk = f"chunk_{index_chunk}"           
    data["entities"].append({
        "label": "TextChunk",
        "attributes": {
            "Id" : id_chunk,
            "text": text
        }
    })
    Utils.add_riferimento_testuale_relation(data, id_chunk, json_ontolgogy)

    current_data_ident = json.dumps(data, indent=2, ensure_ascii=False)
    if current_data_ident is not None:
        print(f"{Fore.WHITE}Data for the category '{category}' with index chunk '{index_chunk}' created successfully!")
        data_file_name = f"JsonData/{category}/{index_chunk}_Data.json"
        # Save the data to the disk as a json file.
        with open(data_file_name, "w", encoding="utf-8") as file:
            file.write(current_data_ident)
        return data
    


def generate_data(category: str, model=None, dataItems=None):
    """
    Generate the Knowledge Graph for the given category (e.g. 'DiscplinaDiUtilizzo').
    """
    if dataItems is not None:
        all_text_paths = [item.text_path for item in dataItems]
    else:
        directory = Path(f"InputPDFtoText/{category}")
        all_text_paths = list(directory.rglob("*.txt"))
    
    if model is None:
        model = "openai/gpt-5-mini"


    print(f"{Fore.GREEN}--Generating the data for the '{category}' category")
    ontology_file = f"Ontologies/{category}/Ontology.json"
    if os.path.exists(ontology_file)==False:
        print(f"{Fore.RED}The ontology file '{ontology_file}' is missing!")
        return
    with open(ontology_file, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    try:
        json_ontology = json.loads(text_ontology)
        text_ontology = json.dumps(json_ontology, ensure_ascii=False)
        ontology = Ontology.from_json(json_ontology)
    except Exception as e:
        print(f"{Fore.RED}Failed to read the ontology '{ontology_file}', error: {e}")
        return
    
    chunks = []
    for text_path in all_text_paths:
        if os.path.exists(text_path)==False:
            print(f"{Fore.RED} The .txt file '{text_path}' does not exist!")
            continue
       
        if category == 'CodiceAppalti':
            chunks = split_codice_appalti(text_path)
            break

        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read()

        chunks.extend(split_text_chunks(text, False))
     

    index_chunk = 0
    json_data_chunks = []
    for chunk in chunks:
        index_chunk = index_chunk + 1
        print(f"--{Fore.WHITE}Processing chunk {index_chunk}/{len(chunks)}.")
        chunk_path = f"JsonData/{category}/{index_chunk}_Data.json"
        if os.path.exists(chunk_path):
            with open(chunk_path, "r", encoding="utf-8") as file:
                load_data_chunk = json.load(file)
                json_data_chunks.append(load_data_chunk)
            print("The chunk has already been processed!")
            continue

        textsToProcess = []
        textsToAdd = []
        textsToProcess.append(chunk)
        limit_while_count = 0
        while(True):
            limit_while_count = limit_while_count + 1
            if limit_while_count>LIMIT_WHILE_LLM:
                print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
                return
            for text in textsToProcess: 
                print(f"{Fore.WHITE}Waiting the LLM response...")
                try:
                    response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.EXTRACT_DATA_PROMPT_ITA.format(ontology=text_ontology, text=chunk)}
                        ]
                    )
                    json_data_chunk = process_reponse_data(category, index_chunk, response, json_ontology, text_ontology, ontology, chunk, model)
                    if json_data_chunk is not None:
                        json_data_chunks.append(json_data_chunk)
                except ContextWindowExceededError as e:
                    mid = len(text) // 2
                    print(f"{Fore.WHITE}Halving the text size from '{len(text)}' to '{mid}'")
                    part1 = text[:mid]
                    part2 = text[mid:]
                    textsToAdd.append(part1)
                    textsToAdd.append(part2)
                    continue
                
            if textsToAdd:
                textsToProcess.clear()
                for textToAdd in textsToAdd:
                    textsToProcess.append(textToAdd)
                textsToAdd.clear()
            else:
                break
    data_file_name = f"JsonData/{category}/Data.json"
    if os.path.exists(data_file_name):
        with open(data_file_name, "r", encoding="utf-8") as file:
            aggregate_Json = json.load(file)
    else:
        aggregate_Json = aggregate_data_and_remove_duplicates(json_data_chunks, json_ontology, text_ontology)
        with open(data_file_name, "w", encoding="utf-8") as file:
            file.write(json.dumps(aggregate_Json, indent=2, ensure_ascii=False))
    json_ontology_with_ref = Utils.get_json_ontology_with_ref(json_ontology, aggregate_Json["entities"])
    ontology_with_ref = Ontology.from_json(json_ontology_with_ref)
    upload_correctly = upload_data(category, aggregate_Json, ontology_with_ref)
    if upload_correctly == False:
        print(f"{Fore.RED}Upload completed with ERRORS for the '{category}' category!")
        return
    else:
        print(f"{Fore.GREEN}Upload completed successfully for the '{category}' category!")


def get_clusters_entities(item_embedding_dict: dict, prob_threshold=0.85):
    """
    It performs the clustering operation on the given entities.
    
    :param item_embedding_dict: dictionary where each key is an entity and the value is the corresponding embedding vector. 
    """
    # Dimensionality Reduction
    entities_string = item_embedding_dict.keys()
    data = np.array(list(item_embedding_dict.values()))


    number_of_entities = len(item_embedding_dict.keys())
    if number_of_entities < 50:
        neighbors_hyparameter = max(3, number_of_entities // 5) # For smaller dataset
    else:
        neighbors_hyparameter = 15 # for bigger dataset

    reducer = umap.UMAP(
        n_neighbors=neighbors_hyparameter, 
        n_components=5,    
        metric='cosine',   
        random_state=42,
        min_dist=0.0
    )
    print("Applying UMAP...")
    embeddings_reduced = reducer.fit_transform(data)


    print("Applying HDBSCAN...")
    clusterer = HDBSCAN(
        min_cluster_size=2,     
        min_samples=1,    
        metric='cosine',
        cluster_selection_method='eom'        
    )
    labels = clusterer.fit_predict(embeddings_reduced)
    probs = clusterer.probabilities_

    count_noise = 0
    count_entities_clustered = 0
    clusters = dict()
    index = 0
    for entity_string, label_cluster in zip(entities_string, labels):
        if label_cluster == -1: # Noise
            count_noise = count_noise + 1
            index = index + 1
            continue      
        if probs[index] > prob_threshold:
            if label_cluster not in clusters:
                clusters[label_cluster] = []
            clusters[label_cluster].append(entity_string)
            count_entities_clustered = count_entities_clustered + 1
        index = index + 1
    print(f"Number of noise entities: {count_noise}")
    print(f"Number of clustered entities: {count_entities_clustered}")
    return clusters



def ask_LLM_merge_duplicated_relations(duplicated_relations, entities, relation_label, source_label, target_label, source_keyref, target_keyref, json_ontology, model=None):
    """
    Given a list of relations, it prompts the LLM to merge any duplicates.
    """
    if model is None:
        model = "openai/gpt-5-nano"

    text_ontology_relation = None
    for relation in json_ontology["relations"]:
        if relation.get("label") == relation_label:
            if relation.get("source").get("label") == source_label:
                if relation.get("target").get("label") == target_label:
                    text_ontology_relation = json.dumps(relation, ensure_ascii=False)
                    break

    print(f"{Fore.WHITE}Asking LLM to merge duplicated relations..")
    duplicated_relations_text = json.dumps(duplicated_relations, ensure_ascii=False)
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": Prompt.MERGE_DUPLICATED_RELATIONS_SYSTEM_ITA},
            {"role": "user",   "content": Prompt.MERGE_DUPLICATED_RELATIONS_PROMPT_ITA.format(relations=duplicated_relations_text, ontology=text_ontology_relation)}
        ]
    )
    response_content = response.choices[0].message["content"].strip()        
    limit_while_count = 0
    while(True):
        limit_while_count = limit_while_count + 1
        if limit_while_count>LIMIT_WHILE_LLM:
            print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
            break
        try:
            new_relation = Utils.validate_single_merged_relation(response_content, entities, relation_label, source_label, target_label, source_keyref, target_keyref, json_ontology)
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. {error}")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.MERGE_DUPLICATED_RELATIONS_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.MERGE_DUPLICATED_RELATIONS_PROMPT_ERROR_ITA.format(duplicated_relations=duplicated_relations_text, ontology=text_ontology_relation, new_relation=response_content, errors=error)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                continue
    return new_relation


def ask_LLM_merge_duplicated_entities(duplicated_entities, entity_label, key_attribute_value, json_ontology, model=None):
    """
    Given a list of entities, it prompts the LLM to merge any duplicates.
    """
    if model is None:
        model = "openai/gpt-5-nano"

    print(f"{Fore.WHITE}Asking LLM to merge duplicated entities..")
    duplicated_entities_text = json.dumps(duplicated_entities, ensure_ascii=False)
    text_ontology_entity = None
    entities = json_ontology["entities"]
    for entity in entities:
        if entity.get("label") == entity_label:          
            text_ontology_entity = json.dumps(entity, ensure_ascii=False)
            break
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": Prompt.MERGE_DUPLICATED_ENTITIES_SYSTEM_ITA},
            {"role": "user",   "content": Prompt.MERGE_DUPLICATED_ENTITIES_PROMPT_ITA.format(entities=duplicated_entities_text, ontology=text_ontology_entity)}
        ]
    )
    response_content = response.choices[0].message["content"].strip()
    limit_while_count = 0
    while(True):
        limit_while_count = limit_while_count + 1
        if limit_while_count>LIMIT_WHILE_LLM:
            print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
            return
        try:
            new_entity = Utils.validate_single_merged_entity(response_content, entity_label, key_attribute_value, json_ontology)
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"{Fore.WHITE}Error extracting JSON. '{error}'")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.MERGE_DUPLICATED_ENTITIES_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.MERGE_DUPLICATED_ENTITIES_PROMPT_ERROR_ITA.format(duplicated_entities=duplicated_entities_text, ontology=text_ontology_entity, new_entity=response_content, errors=error)}       
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                continue
    return new_entity


def refine_with_LLM(category):
    """
    It takes as input a JSON object containing entities, relations, and attributes. Using the embeddings, it identifies similar entities that might be duplicates.
    The duplicates are removed and merged using LLM.
    """
    json_ontology_filename = f"Ontologies/{category}/Ontology.json"
    with open(json_ontology_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    json_data_filename = f"JsonData/{category}/Data.json"
    with open(json_data_filename, "r", encoding="utf-8") as file:
        text_data = file.read()
    json_data = json.loads(text_data)

    # We define a "text description" as an artificial text constructed to describe an entity.
    by_text_desciption_entity_dict = dict() # The key is the entity and the value is the 'text_description'

    label_nameKeyAttribute_dict = Utils.get_dict_label_nameKeyAttribute(json_ontology)
    for index, entity in enumerate(tqdm(json_data["entities"], desc="Constructing the text descriptions")):        
        entity_string = json.dumps(entity, sort_keys=True, ensure_ascii=False)
        entity_id = ""
        text_descritpion = ""
        entity_label = entity.get("label")
        if entity_label == "TextChunk":
            continue     
        text_descritpion = text_descritpion + f"L'entità '{entity_label}'"
        attrs = entity.get("attributes")
        if attrs is not None:  
            num_elements = len(attrs)
            first = True
            for key, value in attrs.items():
                if key == label_nameKeyAttribute_dict[entity_label]:
                    entity_id = value
                if num_elements == 1:
                    text_descritpion = text_descritpion + f" ha l'attributo '{key}' uguale a '{value}'"                   
                    break
                if first:
                    first = False
                    text_descritpion = text_description + " ha gli attributi"
                    text_descritpion = text_descritpion + f" '{key}' uguale a'{value}'"
                else:
                    text_descritpion = text_descritpion + f", '{key}' uguale a '{value}'"              
        text_descritpion = text_descritpion + "."

        for relation in json_data["relations"]:           
            isSource = False
            isTarget = False
            if relation.get("source").get("label") == entity_label:
                if relation.get("source").get("attributes")[label_nameKeyAttribute_dict[entity_label]] == entity_id:
                    isSource = True
            if relation.get("target").get("label") == entity_label:
                if relation.get("target").get("attributes")[label_nameKeyAttribute_dict[entity_label]] == entity_id:
                    isTarget = True
            if isSource == False and isTarget == False:
                continue
            if relation.get("label") == "ESTRATTO_DA_TESTO":
                continue
            label = relation.get("label")
            text_descritpion = text_descritpion + f" Ha la relazione '{label}'"
            source = relation.get("source")
            source_label = source.get("label")
            text_descritpion = text_descritpion + f" che connette '{source_label}'"
            source_attrs = source.get("attributes")
            if source_attrs is not None:
                first = True
                for key, value in source_attrs.items():
                    if first:
                        first = False
                        text_descritpion = text_descritpion + " ("
                        text_descritpion = text_descritpion + f"'{key}' uguale a '{value}'"
                    else:
                        text_descritpion = text_descritpion + f", '{key}' uguale a '{value}'"
                if first == False:
                    text_descritpion = text_descritpion + ")"

            target = relation.get("target")
            target_label = target.get("label")
            text_descritpion = text_descritpion + f" con '{target_label}'"
            target_attrs = target.get("attributes")
            if target_attrs is not None:
                first = True
                for key, value in target_attrs.items():
                    first = False
                    text_descritpion = text_descritpion + " ("
                    text_descritpion = text_descritpion + f"{key} uguale a'{value}'"
                else:
                    text_descritpion = text_descritpion + f", {key} uguale a'{value}'"
                if first == False:
                    text_descritpion = text_descritpion + ")"

            relation_attrs = relation.get("attrs")
            if relation_attrs is not None:
                first = True
                for key, value in relation_attrs.items():
                    if first:
                        first = False
                        text_descritpion = text_descritpion + f" e ha '{key}' uguale a '{value}'"
                    else:
                        text_descritpion = text_descritpion + f", {key} uguale a '{value}'"
            text_descritpion = text_descritpion + "." 
        by_text_desciption_entity_dict[entity_string] = text_descritpion
        


    by_text_description_embedding_entity_dict = dict() # The key is the entity and the value is the embedding of the 'text description'
    for entity_string, text_description in tqdm(by_text_desciption_entity_dict.items(), desc="Constructing the text description embeddings: "):   
        response_embedding = embedding(
            model="text-embedding-3-small",
            input=text_descritpion
        )
        embedded = response_embedding["data"][0]["embedding"]
        by_text_description_embedding_entity_dict[entity_string] = embedded
    entity_partitions = get_clusters_entities(by_text_description_embedding_entity_dict)


    for cluster_id, entities_in_partition in entity_partitions.items():
        ask_LLM_merge_similar_entities(entities_in_partition, json_data, json_ontology)
    print(f"{Fore.GREEN}-- KG refinement '{category}' completed!")





def ask_LLM_merge_similar_entities(similar_entities: str, json_data, json_ontology, model=None):
    """
    Given a list of entities, it prompts the LLM to merge any duplicates (if any).
    """
    if model is None:
        model = "openai/gpt-5-nano"

    print(f"\n\n{Fore.WHITE}--NEW CLUSTER--")
    label_nameKeyAttribute_dict = Utils.get_dict_label_nameKeyAttribute(json_ontology)

    cluster = copy.deepcopy(json_data)
    cluster["entities"].clear()
    cluster["relations"].clear()
    text = ""
    for similar_entity in similar_entities:
        for entity in json_data["entities"]:
            entity_string = json.dumps(entity, sort_keys=True, ensure_ascii=False)
            if entity_string == similar_entity:
                cluster["entities"].append(entity)
                break



    estratto_da_relations_string = []
    chunk_ids = []  
    for entity in tqdm(list(cluster["entities"]), desc="Constructing the cluster: "):
        entity_label = entity.get("label")
        entity_id = ""
        attrs = entity.get("attributes")
        if attrs is not None:  
            for key, value in attrs.items():
                if key == label_nameKeyAttribute_dict[entity_label]:
                    entity_id = value
                    break

        for relation in json_data["relations"]:           
            isSource = False
            isTarget = False
            if relation.get("source").get("label") == entity_label:
                if relation.get("source").get("attributes")[label_nameKeyAttribute_dict[entity_label]] == entity_id:
                    isSource = True
            if relation.get("target").get("label") == entity_label:
                if relation.get("target").get("attributes")[label_nameKeyAttribute_dict[entity_label]] == entity_id:
                    isTarget = True
            if isSource == False and isTarget == False:
                continue

            if relation.get("label") == "ESTRATTO_DA_TESTO":
                estratto_da_relations_string.append(json.dumps(relation, sort_keys=True, ensure_ascii=False))
                for key, value in relation.get("target").get("attributes").items():
                    if key == "Id":
                        if value not in chunk_ids:
                            chunk_ids.append(value)   
                        break            
                continue
            
            entity_label_to_find = ""
            entity_id_to_find = ""
            if isSource:
                entity_label_to_find = relation.get("target").get("label")
                for key, value in relation.get("target").get("attributes").items():
                    if key == label_nameKeyAttribute_dict[entity_label_to_find]:
                        entity_id_to_find = value
                        break
            else:
                entity_label_to_find = relation.get("source").get("label")
                for key, value in relation.get("source").get("attributes").items():
                    if key == label_nameKeyAttribute_dict[entity_label_to_find]:
                        entity_id_to_find = value
                        break
            entity_found = False
            for entity in json_data["entities"]:
                if entity.get("label") != entity_label_to_find:
                    continue
                for key, value in entity.get("attributes").items():
                    if key == label_nameKeyAttribute_dict[entity_label_to_find]:
                        if value == entity_id_to_find:
                            cluster["entities"].append(entity)
                            cluster["relations"].append(relation)
                            entity_found = True
                            break
                if entity_found:
                    break


    text_ontology = json.dumps(json_ontology, ensure_ascii=False)
    ontology = Ontology.from_json(json_ontology)
    text_data = json.dumps(cluster, ensure_ascii=False)

    for chunk_id in chunk_ids:
        for entity in json_data["entities"]:
            if entity.get("label") == "TextChunk":
               if entity.get("attributes")["Id"] == chunk_id:
                   text = text + entity.get("attributes")["text"]

    print(f"{Fore.WHITE}Asking LLM to merge similar entities..")
    response = completion(
        model=model,
        messages=[
            {"role": "system", "content": Prompt.MERGE_SIMILAR_ENTITIES_SYSTEM_ITA},
            {"role": "user",   "content": Prompt.MERGE_SIMILAR_ENTITIES_PROMPT_ITA.format(data=text_data, text=text, ontology=text_ontology)}
        ]
    )
    response_content = response.choices[0].message["content"].strip()
    if response_content.lower() == "none":
        print("No duplicate entities found by the LLM.!")
        return
    limit_while_count = 0
    while(True):
        limit_while_count = limit_while_count + 1
        if limit_while_count>LIMIT_WHILE_LLM:
            print(f"{Fore.RED} -----------LIMIT_WHILE_LLM exceeded!!---------")
            return None
        try:
            new_data = Utils.get_json_data(response_content, ontology, json_ontology)
            break
        except Exception as e:
            try:
                # fallback 
                error = f"TypeError: '{type(e)}', error: '{e}'"
                print(f"Error extracting JSON. {error}")
                print(f"Prompting model to fix JSON")
                json_fix_response = completion(
                        model=model,
                        messages=[
                            {"role": "system", "content": Prompt.EXTRACT_DATA_SYSTEM_ITA},
                            {"role": "user",   "content": Prompt.FIX_JSON_PROMPT_DATA_ITA.format(errors=error, json=response_content, text=text, ontology=text_ontology)}         
                        ]
                    )
                response_content = json_fix_response.choices[0].message["content"].strip()
            except Exception as e:
                continue


    # We remove the cluster
    for cluster_entity in cluster["entities"]:
        entity_to_remove = None
        for old_entity in json_data["entities"]:
            if json.dumps(old_entity, sort_keys=True, ensure_ascii=False) == cluster_entity:
                entity_to_remove = old_entity
                break
        if entity_to_remove is not None:
            json_data["entities"].remove(entity_to_remove)

    for cluster_relation in cluster["relations"]:
        relation_to_remove = None
        for old_relation in json_data["relations"]:
            if json.dumps(old_relation, sort_keys=True, ensure_ascii=False) == json.dumps(cluster_relation, sort_keys=True, ensure_ascii=False):
                relation_to_remove = old_entity
                break
        if relation_to_remove is not None:
            json_data["relations"].remove(relation_to_remove)

    for estratto_relation in estratto_da_relations_string:
        relation_to_remove = None
        for old_relation in json_data["relations"]:
            if json.dumps(old_relation, sort_keys=True, ensure_ascii=False) == json.dumps(estratto_relation, sort_keys=True, ensure_ascii=False):
                relation_to_remove = old_entity
                break
        if relation_to_remove is not None:
            json_data["relations"].remove(relation_to_remove)
    
    # We add the new data
    for chunk_id in chunk_ids:
        Utils.add_riferimento_testuale_relation(new_data, chunk_id, json_ontology)
    json_data["entities"].extend(new_data["entities"])
    json_data["relations"].extend(new_data["relations"])







def aggregate_data_and_remove_duplicates(json_data_list : list, json_ontology, text_ontology: str):
    """
    Takes as input a list of JSON objects containing entities, relations, and attributes related to a specific .txt file, and merges them into a single JSON object.
    It also detects and merges entities and relations that are duplicated.
    """

    print("--Removing duplicate entity and relations")
    all_entities = []
    all_relations = []

    for chunk in json_data_list:
        all_entities.extend(chunk.get('entities', []))
        all_relations.extend(chunk.get('relations', []))

    json_data = {
        "entities": all_entities,
        "relations": all_relations
    }
        

    entities_duplicated_tuples = Utils.get_duplicated_entity_as_tuples(json_data["entities"], json_ontology)
    relations_duplicated_tuples = Utils.get_duplicated_relations_as_tuples(json_data["relations"], json_ontology)
    label_nameKey_dict = Utils.get_dict_label_nameKeyAttribute(json_ontology)


    for entity_label, entity_id, json_entities in entities_duplicated_tuples:
        for json_entity in json_entities:
            json_data['entities'].remove(json_entity)
        entity_to_save = None 
        entity_to_save = ask_LLM_merge_duplicated_entities(json_entities.copy(), entity_label, entity_id, json_ontology)
        print("New entity created!")
        json_data['entities'].append(entity_to_save)
    

    for relation_label, source_label, target_label, source_keyref, target_keyref, json_relations in relations_duplicated_tuples:
        for json_relation in json_relations:
            json_data['relations'].remove(json_relation)
        relation_to_save = None 
        relation_to_save = ask_LLM_merge_duplicated_relations(json_relations.copy(), json_data["entities"], relation_label, source_label, target_label, source_keyref, target_keyref, json_ontology) 
        print("New relation created!")      
        json_data['relations'].append(relation_to_save)
    return json_data


def upload_data(category, jsonData, ontology):
    """
    It uploads the given data to FalkorDB. It returns 'True' on success; 'False' on failure.
    """
    client = FalkorDB(**client_kwargs)
    graph = client.select_graph(category)

    
    print(f"{Fore.WHITE}Uploading the data...")
    hasErrors=False
        
    for entity in jsonData["entities"]:
        try:
            extract_data_step.create_entity(graph, entity, ontology)
        except Exception as e:
            print(f"{Fore.RED}Error creating entity: {e}")
            hasErrors=True
            continue

    for relation in jsonData["relations"]:
        try:
            extract_data_step.create_relation(graph, relation, ontology)
        except Exception as e:
            print(f"{Fore.RED}Error creating relation: {e}")
            hasErrors=True
            continue

    if hasErrors==False:
        return True
    else:
        return False




def create_dir():
    os.makedirs("Ontologies",  exist_ok=True)    
    os.makedirs("Ontologies/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("Ontologies/Normativa",  exist_ok=True)    
    os.makedirs("Ontologies/FAQ",  exist_ok=True)    
    os.makedirs("Ontologies/GuidePraticheOE",  exist_ok=True) 
    os.makedirs("Ontologies/GuidePraticheSA",  exist_ok=True) 
    os.makedirs("Ontologies/CodiceAppalti",  exist_ok=True) 


    os.makedirs("JsonData",  exist_ok=True)    
    os.makedirs("JsonData/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("JsonData/Normativa",  exist_ok=True)    
    os.makedirs("JsonData/FAQ",  exist_ok=True)    
    os.makedirs("JsonData/GuidePraticheOE",  exist_ok=True) 
    os.makedirs("JsonData/GuidePraticheSA",  exist_ok=True) 
    os.makedirs("JsonData/CodiceAppalti",  exist_ok=True) 

    os.makedirs("InputPDFtoText",  exist_ok=True)   
    os.makedirs("InputPDFtoText/DisciplinaDiUtilizzo",  exist_ok=True)    
    os.makedirs("InputPDFtoText/Normativa",  exist_ok=True)    
    os.makedirs("InputPDFtoText/FAQ",  exist_ok=True)    
    os.makedirs("InputPDFtoText/GuidePraticheOE",  exist_ok=True) 
    os.makedirs("InputPDFtoText/GuidePraticheSA",  exist_ok=True) 
    os.makedirs("InputPDFtoText/CodiceAppalti",  exist_ok=True) 




def main():
    create_dir()
    pdf_dict = {} # The key is the category (DiscplinaDiUtilizzo, GuidePraticheOE,...) and the value is the list of associated .pdf files

    # DiscplinaDiUtilizzo
    dir_DisciplinaDiUtilizzo = Path("InputPDF/DisciplinaDiUtilizzo")
    pdf_paths_DisciplinaDiUtilizzo = list(dir_DisciplinaDiUtilizzo.rglob("*.pdf"))
    pdf_dict["DisciplinaDiUtilizzo"] = pdf_paths_DisciplinaDiUtilizzo

    # GuidePraticheOE
    dir_GuidePraticheOE = Path("InputPDF/GuidePraticheOE")
    pdf_paths_GuidePraticheOE= list(dir_GuidePraticheOE.rglob("*.pdf"))
    pdf_dict["GuidePraticheOE"] = pdf_paths_GuidePraticheOE

    # GuidePraticheSA
    dir_GuidePraticheSA = Path("InputPDF/GuidePraticheSA")
    pdf_paths_GuidePraticheSA= list(dir_GuidePraticheSA.rglob("*.pdf"))
    pdf_dict["GuidePraticheSA"] = pdf_paths_GuidePraticheSA


    # Normativa
    dir_Normativa = Path("InputPDF/Normativa")
    pdf_paths_Normativa= list(dir_Normativa.rglob("*.pdf"))
    pdf_dict["Normativa"] = pdf_paths_Normativa


    while(True):
        print(f"{Fore.WHITE}---------------------------")
        print("1. Preprocess all the .pdf files converting them to .txt files")
        print("2. Generate the ontologies")
        print("3. Load the data")
        print("4. Refine the data removing similar entities from the KG using LLM")
        print("5. Exit")
        choice = int(input("What do you want to do? "))
        if choice == 1:
            dataItems = preprocess_pdf(pdf_dict)
        elif choice == 2:
            while(True):
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePraticheOE")
                print("3. GuidePraticheSA")
                print("4. Normativa")
                print("5. FAQ")
                print("6. CodiceAppalti")
                choice_cat = int(input("For which category do you want to generate the ontology? "))
                if choice_cat == 1:
                    generate_ontology("DisciplinaDiUtilizzo")
                    break
                elif choice_cat == 2:
                    generate_ontology("GuidePraticheOE")
                    break
                elif choice_cat == 3:
                    generate_ontology("GuidePraticheSA")
                    break
                elif choice_cat == 4:                    
                    generate_ontology("Normativa")
                    break
                elif choice_cat == 5:
                    generate_ontology("FAQ")
                    break
                elif choice_cat == 6:
                    generate_ontology("CodiceAppalti")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 3:
            while(True):             
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePraticheOE")
                print("3. GuidePraticheSA")
                print("4. Normativa")
                print("5. FAQ")
                print("6. CodiceAppalti")
                choice_cat = int(input("For which category do you want to generate the data? "))
                if choice_cat == 1:
                    generate_data("DisciplinaDiUtilizzo")
                    break
                elif choice_cat == 2:
                    generate_data("GuidePraticheOE")
                    break
                elif choice_cat == 3:
                    generate_data("GuidePraticheSA")
                    break
                elif choice_cat == 4:
                    generate_data("Normativa")
                    break
                elif choice_cat == 5:
                    generate_data("FAQ")
                    break
                elif choice_cat == 6:
                    generate_data("CodiceAppalti")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 4:
           # QUI MI DOVREI SDOGANARE DAL CONCETTO DI ONTOLOGIA DATO CHE L'ONTOLOGIA è ASSOCIATA A CIASCUN TEXT FILE (non a intera categoria)
            # DEVO SEMPLICEMENTE FARE CLUSTERING, FAR DECIDERE A LLM COSA MERGIARE E FARE DIRETTAMENTE QUERY CHYPER PER CAMBIARE KG FINALE. 
            while(True):             
                print("1. DisciplinaDiUtilizzo")
                print("2. GuidePraticheOE")
                print("3. GuidePraticheSA")
                print("4. Normativa")
                print("5. FAQ")
                print("6. CodiceAppalti")
                choice_cat = int(input("For which category do you want to refine the KG? "))
                if choice_cat == 1:
                    refine_with_LLM("DisciplinaDiUtilizzo")
                    break
                elif choice_cat == 2:
                    refine_with_LLM("GuidePraticheOE")
                    break
                elif choice_cat == 3:
                    refine_with_LLM("GuidePraticheSA")
                    break
                elif choice_cat == 4:
                    refine_with_LLM("Normativa")
                    break
                elif choice_cat == 5:
                    refine_with_LLM("FAQ")
                    break
                elif choice_cat == 6:
                    refine_with_LLM("CodiceAppalti")
                    break
                else:
                    print("Invalid choice. Please try again")
        elif choice == 5:
            break
        else:
            print("Invalid choice. Please try again")
        

if __name__ == "__main__":
    main()