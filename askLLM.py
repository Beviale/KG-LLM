from dotenv import load_dotenv
from graphrag_sdk.source import URL
from graphrag_sdk import KnowledgeGraph
from graphrag_sdk.model_config import KnowledgeGraphModelConfig
from pathlib import Path
import Prompt
import os
from falkordb import FalkorDB
from colorama import init, Fore, Style
import numpy as np
from graphrag_sdk.orchestrator import Orchestrator
from graphrag_sdk.agents.kg_agent import KGAgent
from graphrag_sdk.fixtures import prompts as promptsSDK # Original Prompt
from graphrag_sdk.models.litellm import LiteModel
from graphrag_sdk import KnowledgeGraph, Ontology
import json
from contextlib import redirect_stdout

init(autoreset=True)

load_dotenv()

host = os.getenv("FALKORDB_HOST", "localhost")
port = int(os.getenv("FALKORDB_PORT", "6379"))
username = os.getenv("FALKORDB_USERNAME", "beviale")  
password = os.getenv("FALKORDB_PASSWORD", "12345678Ab!") 

client_kwargs = {
    "host": host,
    "port": port,
}


def translate_sdkPrompts_to_ITA():
    promptsSDK.ORCHESTRATOR_DECISION_PROMPT = Prompt.ORCHESTRATOR_DECISION_PROMPT_ITA 
    promptsSDK.ORCHESTRATOR_SUMMARY_PROMPT = Prompt.ORCHESTRATOR_SUMMARY_PROMPT_ITA
    promptsSDK.ORCHESTRATOR_EXECUTION_PLAN_PROMPT = Prompt.ORCHESTRATOR_EXECUTION_PLAN_PROMPT_ITA
    promptsSDK.ORCHESTRATOR_SYSTEM = Prompt.ORCHESTRATOR_SYSTEM_ITA
    promptsSDK.GRAPH_QA_PROMPT = Prompt.GRAPH_QA_PROMPT_ITA
    promptsSDK.GRAPH_QA_SYSTEM = Prompt.GRAPH_QA_SYSTEM_ITA
    promptsSDK.CYPHER_GEN_PROMPT_WITH_HISTORY = Prompt.CYPHER_GEN_PROMPT_WITH_HISTORY_ITA
    promptsSDK.CYPHER_GEN_PROMPT_WITH_ERROR = Prompt.CYPHER_GEN_PROMPT_WITH_ERROR_ITA
    promptsSDK.CYPHER_GEN_PROMPT = Prompt.CYPHER_GEN_PROMPT_ITA
    promptsSDK.CYPHER_GEN_SYSTEM = Prompt.CYPHER_GEN_SYSTEM_ITA
    promptsSDK.COMPLETE_DATA_EXTRACTION = Prompt.COMPLETE_DATA_EXTRACTION_ITA





def istantiate_KG_and_Agents(model_name="openai/gpt-5-nano"):
    """
    It instantiates all the Knowledge Graphs and the associated agents. It returns the Orchestrator object with all the agents created registered to it.
    """
    #NOTA: ontologia viene estratta automaticamente
    #client = FalkorDB(**client_kwargs)
    model = LiteModel(model_name=model_name)

    codice_appalti_ontology_filename = "Ontologies/CodiceAppalti/Ontology.json"
    with open(codice_appalti_ontology_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    codice_appalti_ontology = Ontology.from_json(json_ontology)
    codice_appalti = KnowledgeGraph(
        name="CodiceAppalti",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        ontology=codice_appalti_ontology
    )
    if codice_appalti==None:
        print(f"{Fore.RED} An error occured while instantiating the 'CodiceAppalti' KG!")
        return None
    print("'CodiceAppalti' Ok!")
    
    disciplina_di_utilizzo_ontology_filename = "Ontologies/DisciplinaDiUtilizzo/Ontology.json"
    with open(disciplina_di_utilizzo_ontology_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    disciplina_di_utilizzo_ontology = Ontology.from_json(json_ontology)
    disciplina_utilizzo = KnowledgeGraph(
        name="DisciplinaDiUtilizzo",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        ontology=disciplina_di_utilizzo_ontology
    )
    if disciplina_utilizzo==None:
        print(f"{Fore.RED} An error occured while instantiating the 'DisciplinaDiUtilizzo' KG!")
        return None
    print("'DisciplinaDiUtilizzo' Ok!")


    faq_ontology_filename = "Ontologies/FAQ/Ontology.json"
    with open(faq_ontology_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    faq_ontology = Ontology.from_json(json_ontology)
    faq = KnowledgeGraph(
        name="FAQ",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        ontology=faq_ontology
    )
    if faq==None:
        print(f"{Fore.RED} An error occured while instantiating the 'FAQ' KG!")
        return None
    print("'FAQ' Ok!")


    guide_pratiche_oe_filename = "Ontologies/GuidePraticheOE/Ontology.json"
    with open(guide_pratiche_oe_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    guide_pratiche_oe_ontology = Ontology.from_json(json_ontology)
    guide_pratiche_oe = KnowledgeGraph(
        name="GuidePraticheOE",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        ontology=guide_pratiche_oe_ontology 
    )
    if guide_pratiche_oe==None:
        print(f"{Fore.RED} An error occured while instantiating the 'GuidePraticheOE' KG!")
        return None
    print("'GuidePraticheOE' Ok!")
 
    guide_pratiche_sa_filename = "Ontologies/GuidePraticheSA/Ontology.json"
    with open(guide_pratiche_sa_filename, "r", encoding="utf-8") as file:
        text_ontology = file.read()
    json_ontology = json.loads(text_ontology)
    guide_pratiche_sa_ontology = Ontology.from_json(json_ontology)
    guide_pratiche_sa = KnowledgeGraph(
        name="GuidePraticheSA",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        ontology=guide_pratiche_sa_ontology 
    )
    if guide_pratiche_sa==None:
        print(f"{Fore.RED} An error occured while instantiating the 'GuidePraticheSA' KG!")
        return None
    print("'GuidePraticheSA' Ok!")


    """
    normativa = KnowledgeGraph(
        name="Normativa",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        username=username, 
        password=password 
    )
    if normativa==None:
        print(f"{Fore.RED} An error occured while instantiating the 'Normativa' KG!")
        return None
    """

    codice_appalti_agent = KGAgent(
        agent_id="CodiceAppaltiAgent",
        kg=codice_appalti,
        introduction="Sono un agente esperto nel rispondere a domande relative all'intero Codice degli appalti italiano.",
    )
    
    disciplina_utilizzo_agent = KGAgent(
        agent_id="DisciplinaDiUtilizzoAgent",
        kg=disciplina_utilizzo,
        introduction="Sono un agente esperto nel rispondere a domande relative alla disciplina di utilizzo della piattaforma EmPULIA. La disciplina di utilizzo spiega le modalità di fruizione dei servizi applicativi e delle funzionalità della piattaforma EmPULIA del soggetto aggregatore della Regione Puglia.",
    )
    guide_praticheOE_Agent = KGAgent(
        agent_id="GuidePraticheOEAgent",
        kg=guide_pratiche_oe,
        introduction="Sono un agente esperto nel rispondere a domande relative alle guide pratiche della piattaforma EmPULIA. Le guide pratiche sono dei manuali d'uso dettagliati e sempre aggiornati per facilitare - mediante l'utilizzo di percorsi guidati - tutte le operazioni effettuabili on line sulla piattaforma di E-Procurement EmPULIA. Posso rispondere soltanto a domande relative agli operatori economici",
    )
    
    guide_praticheSA_Agent = KGAgent(
        agent_id="GuidePraticheSAAgent",
        kg=guide_pratiche_sa,
        introduction="Sono un agente esperto nel rispondere a domande relative alle guide pratiche della piattaforma EmPULIA. Le guide pratiche sono dei manuali d'uso dettagliati e sempre aggiornati per facilitare - mediante l'utilizzo di percorsi guidati - tutte le operazioni effettuabili on line sulla piattaforma di E-Procurement EmPULIA. Posso rispondere soltanto a domande relative alle stazioni appaltanti.",
    )
    
    """
    normativa_agent = KGAgent(
        agent_id="NormativaAgent",
        kg=normativa,
        introduction="Sono un agente esperto nel rispondere a domande relative alle principali questioni normative sugli appalti pubblici. Sono esperto nelle principali norme che regolano gli appalti pubblici, il Programma nazionale di razionalizzazione della spesa pubblica e gli strumenti elettronici d'acquisto.",
    )
    """
    faq_agent = KGAgent(
        agent_id="FAQagent",
        kg=faq,
        introduction="Sono un agente esperto nel rispondere alle FAQ relative alla piattaforma EmPulia. Le Frequently Asked Questions, meglio conosciute con la sigla FAQ, sono letteralmente le 'domande poste frequentemente'; più esattamente sono una serie di risposte stilate direttamente dall'helpdesk, in risposta alle domande che vengono poste più frequentemente dagli utilizzatori del servizio EmPULIA.",
    )



    # Initialize the orchestrator while giving it the backstory.
    orchestrator = Orchestrator(
        model,
        backstory=Prompt.BACKSTORY_ORCHESTRATOR_ITA,
    )

    # Register the agents that we created above.
    orchestrator.register_agent(codice_appalti_agent)
    print("'CodiceAppalti' agent Ok!")
    orchestrator.register_agent(disciplina_utilizzo_agent)
    print("'DisciplinaDiUtilizzo' agent Ok!")
    orchestrator.register_agent(guide_praticheOE_Agent)
    print("'GuidePraticheOE' agent Ok!")
    orchestrator.register_agent(guide_praticheSA_Agent)
    print("'GuidePraticheSA' agent Ok!")
    #orchestrator.register_agent(normativa_agent)
    orchestrator.register_agent(faq_agent)
    print("'FAQ' agent Ok!")

    return orchestrator


def run_orchestrator(orchestrator, question):
    print("Asking the orchestrator...")
    runner = orchestrator.ask(question)
    return runner.output


def ask(question, orchestrator=None):
    if orchestrator is None:
        orchestrator = initialize()
        if orchestrator is None:
            print("Orchestrator is None!")
            return
    return orchestrator, run_orchestrator(orchestrator, question)



def initialize():
    translate_sdkPrompts_to_ITA()
    print(f"{Fore.GREEN}Loading the Knowledge Graphs and the agents...")
    orchestrator = istantiate_KG_and_Agents()
    return orchestrator

    
def main():
    orchestrator = None
    while(True):
        question = input("Question (-1 to exit): ")
        if question == "-1":
            break
        orchestrator, answer = ask(question, orchestrator)
        if answer is not None:
            print("Answer: "  + answer)



if __name__ == "__main__":
    main()