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

    
    codice_appalti = KnowledgeGraph(
        name="CodiceAppalti",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        username=username, 
        password=password 
    )
    if codice_appalti==None:
        print(f"{Fore.RED} An error occured while instantiating the 'CodiceAppalti' KG!")
        return None


    disciplina_utilizzo = KnowledgeGraph(
        name="DisciplinaDiUtilizzo",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        username=username, 
        password=password 
    )
    if disciplina_utilizzo==None:
        print(f"{Fore.RED} An error occured while instantiating the 'DisciplinaDiUtilizzo' KG!")
        return None


    faq = KnowledgeGraph(
        name="FAQ",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        username=username, 
        password=password 
    )
    if faq==None:
        print(f"{Fore.RED} An error occured while instantiating the 'FAQ' KG!")
        return None

    guide_pratiche = KnowledgeGraph(
        name="GuidePratiche",
        model_config=KnowledgeGraphModelConfig.with_model(model),
        host=host,
        port=port,
        username=username, 
        password=password 
    )
    if guide_pratiche==None:
        print(f"{Fore.RED} An error occured while instantiating the 'GuidePratiche' KG!")
        return None

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
    guide_pratiche_Agent = KGAgent(
        agent_id="GuidePraticheAgent",
        kg=guide_pratiche,
        introduction="Sono un agente esperto nel rispondere a domande relative alle guide pratiche della piattaforma EmPULIA. Le guide pratiche sono dei manuali d'uso dettagliati e sempre aggiornati per facilitare - mediante l'utilizzo di percorsi guidati - tutte le operazioni effettuabili on line sulla piattaforma di E-Procurement EmPULIA.",
    )
    normativa_agent = KGAgent(
        agent_id="NormativaAgenty",
        kg=normativa,
        introduction="Sono un agente esperto nel rispondere a domande relative alle principali questioni normative sugli appalti pubblici. Sono esperto nelle principali norme che regolano gli appalti pubblici, il Programma nazionale di razionalizzazione della spesa pubblica e gli strumenti elettronici d'acquisto.";
    )
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
    orchestrator.register_agent(disciplina_utilizzo_agent)
    orchestrator.register_agent(guide_pratiche_Agent)
    orchestrator.register_agent(normativa_agent)
    orchestrator.register_agent(faq_agent)

    return orchestrator


def run_orchestrator(orchestrator, question):

    # Query the orchestrator.
    runner = orchestrator.ask("Create a two-day itinerary for a trip to Rome. Please don't ask me any questions; just provide the best itinerary you can.")
    print(runner.output)

    
def main():
    translate_sdkPrompts_to_ITA()
    print(f"{Fore.GREEN}Loading the Knowledge Graphs and the agents...")
    orchestrator = istantiate_KG_and_Agents()
    if orchestrator is None:
        return
    print(f"Question: ")
    question = int(input("What do you want to do? "))
    run_orchestrator(orchestrator, question)


if __name__ == "__main__":
    main()