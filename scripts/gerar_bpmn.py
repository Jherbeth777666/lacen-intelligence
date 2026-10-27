"""
Gera os diagramas BPMN 2.0 (XML) para visualização na interface.
Reflete o pipeline real: Captura de Foto -> Gemini OCR -> Google Sheets -> Robô RPA Python -> Coleta.
"""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_BPMN = RAIZ / "app" / "static" / "bpmn"


def gerar_processo_atual() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
  id="Definitions_Atual" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_Atual">
    <bpmn:participant id="Participant_Processo" name="Fluxo Operacional LACEN (Pipeline Automatizado com IA e RPA)" processRef="Process_Atual" />
  </bpmn:collaboration>

  <bpmn:process id="Process_Atual" isExecutable="false">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_UBS" name="UBS">
        <bpmn:flowNodeRef>Start_Solicitacao</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_Agendamento</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Recepcao" name="LACEN · Recepção e triagem">
        <bpmn:flowNodeRef>Gateway_Compareceu</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_VagaPerdida</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_FotoRequisicao</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_GeminiOCR</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_SheetsStaging</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_RoboRPA</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_Coleta</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Lab" name="LACEN · Laboratório">
        <bpmn:flowNodeRef>Gateway_Amostra</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_Processar</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_Laudo</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_Sucesso</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="Lane_Gestao" name="Gestão">
        <bpmn:flowNodeRef>Task_Relatorios</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End_Gestao</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>

    <!-- Nós -->
    <bpmn:startEvent id="Start_Solicitacao" name="Solicitação médica" />
    <bpmn:userTask id="Task_Agendamento" name="Agendar em planilha (UBS)" />
    
    <bpmn:exclusiveGateway id="Gateway_Compareceu" name="Paciente compareceu?" />
    <bpmn:endEvent id="End_VagaPerdida" name="Vaga perdida" />
    
    <bpmn:userTask id="Task_FotoRequisicao" name="Capturar foto da requisição" />
    <bpmn:serviceTask id="Task_GeminiOCR" name="Extração estruturada (Gemini API)" />
    <bpmn:serviceTask id="Task_SheetsStaging" name="Organização dos dados (Sheets)" />
    <bpmn:scriptTask id="Task_RoboRPA" name="Cadastro automático Worklab (Robô Python)" />
    <bpmn:userTask id="Task_Coleta" name="Coleta da amostra" />

    <bpmn:exclusiveGateway id="Gateway_Amostra" name="Amostra adequada?" />
    <bpmn:serviceTask id="Task_Processar" name="Processar e analisar" />
    <bpmn:userTask id="Task_Laudo" name="Liberar laudo no sistema" />
    <bpmn:endEvent id="End_Sucesso" name="Resultado liberado" />

    <bpmn:userTask id="Task_Relatorios" name="Consolidação e cruzamento analítico" />
    <bpmn:endEvent id="End_Gestao" name="Painel atualizado" />

    <!-- Sequência -->
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_Solicitacao" targetRef="Task_Agendamento" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_Agendamento" targetRef="Gateway_Compareceu" />
    <bpmn:sequenceFlow id="Flow_Nao" name="Não" sourceRef="Gateway_Compareceu" targetRef="End_VagaPerdida" />
    <bpmn:sequenceFlow id="Flow_Sim" name="Sim" sourceRef="Gateway_Compareceu" targetRef="Task_FotoRequisicao" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_FotoRequisicao" targetRef="Task_GeminiOCR" />
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Task_GeminiOCR" targetRef="Task_SheetsStaging" />
    <bpmn:sequenceFlow id="Flow_5" sourceRef="Task_SheetsStaging" targetRef="Task_RoboRPA" />
    <bpmn:sequenceFlow id="Flow_6" sourceRef="Task_RoboRPA" targetRef="Task_Coleta" />
    <bpmn:sequenceFlow id="Flow_7" sourceRef="Task_Coleta" targetRef="Gateway_Amostra" />
    <bpmn:sequenceFlow id="Flow_Recoleta" name="Não (recoleta)" sourceRef="Gateway_Amostra" targetRef="Task_Coleta" />
    <bpmn:sequenceFlow id="Flow_Adequada" name="Sim" sourceRef="Gateway_Amostra" targetRef="Task_Processar" />
    <bpmn:sequenceFlow id="Flow_8" sourceRef="Task_Processar" targetRef="Task_Laudo" />
    <bpmn:sequenceFlow id="Flow_9" sourceRef="Task_Laudo" targetRef="End_Sucesso" />
    <bpmn:sequenceFlow id="Flow_10" sourceRef="Task_Laudo" targetRef="Task_Relatorios" />
    <bpmn:sequenceFlow id="Flow_11" sourceRef="Task_Relatorios" targetRef="End_Gestao" />
  </bpmn:process>

  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_Atual">
      <bpmndi:BPMNShape id="Participant_Di" bpmnElement="Participant_Processo" isHorizontal="true">
        <dc:Bounds x="160" y="80" width="1380" height="660" />
      </bpmndi:BPMNShape>
      
      <bpmndi:BPMNShape id="Lane_UBS_Di" bpmnElement="Lane_UBS" isHorizontal="true">
        <dc:Bounds x="190" y="80" width="1350" height="150" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_Recepcao_Di" bpmnElement="Lane_Recepcao" isHorizontal="true">
        <dc:Bounds x="190" y="230" width="1350" height="180" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_Lab_Di" bpmnElement="Lane_Lab" isHorizontal="true">
        <dc:Bounds x="190" y="410" width="1350" height="170" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_Gestao_Di" bpmnElement="Lane_Gestao" isHorizontal="true">
        <dc:Bounds x="190" y="580" width="1350" height="160" />
      </bpmndi:BPMNShape>

      <!-- Elementos UBS -->
      <bpmndi:BPMNShape id="Start_Solicitacao_Di" bpmnElement="Start_Solicitacao">
        <dc:Bounds x="232" y="132" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_Agendamento_Di" bpmnElement="Task_Agendamento">
        <dc:Bounds x="310" y="110" width="120" height="80" />
      </bpmndi:BPMNShape>

      <!-- Recepção e Triagem -->
      <bpmndi:BPMNShape id="Gateway_Compareceu_Di" bpmnElement="Gateway_Compareceu" isMarkerVisible="true">
        <dc:Bounds x="475" y="295" width="50" height="50" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_VagaPerdida_Di" bpmnElement="End_VagaPerdida">
        <dc:Bounds x="482" y="132" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_FotoRequisicao_Di" bpmnElement="Task_FotoRequisicao">
        <dc:Bounds x="560" y="280" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_GeminiOCR_Di" bpmnElement="Task_GeminiOCR">
        <dc:Bounds x="710" y="280" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_SheetsStaging_Di" bpmnElement="Task_SheetsStaging">
        <dc:Bounds x="860" y="280" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_RoboRPA_Di" bpmnElement="Task_RoboRPA">
        <dc:Bounds x="1010" y="280" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_Coleta_Di" bpmnElement="Task_Coleta">
        <dc:Bounds x="1160" y="280" width="110" height="80" />
      </bpmndi:BPMNShape>

      <!-- Laboratório -->
      <bpmndi:BPMNShape id="Gateway_Amostra_Di" bpmnElement="Gateway_Amostra" isMarkerVisible="true">
        <dc:Bounds x="1190" y="465" width="50" height="50" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_Processar_Di" bpmnElement="Task_Processar">
        <dc:Bounds x="1270" y="450" width="110" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_Laudo_Di" bpmnElement="Task_Laudo">
        <dc:Bounds x="1400" y="450" width="100" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_Sucesso_Di" bpmnElement="End_Sucesso">
        <dc:Bounds x="1432" y="370" width="36" height="36" />
      </bpmndi:BPMNShape>

      <!-- Gestão -->
      <bpmndi:BPMNShape id="Task_Relatorios_Di" bpmnElement="Task_Relatorios">
        <dc:Bounds x="1390" y="620" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_Gestao_Di" bpmnElement="End_Gestao">
        <dc:Bounds x="1432" y="730" width="36" height="36" />
      </bpmndi:BPMNShape>

      <!-- Conexões -->
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="268" y="150" /><di:waypoint x="310" y="150" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="430" y="150" /><di:waypoint x="450" y="150" /><di:waypoint x="450" y="320" /><di:waypoint x="475" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_Nao_di" bpmnElement="Flow_Nao">
        <di:waypoint x="500" y="295" /><di:waypoint x="500" y="168" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_Sim_di" bpmnElement="Flow_Sim">
        <di:waypoint x="525" y="320" /><di:waypoint x="560" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_3_di" bpmnElement="Flow_3">
        <di:waypoint x="680" y="320" /><di:waypoint x="710" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_4_di" bpmnElement="Flow_4">
        <di:waypoint x="830" y="320" /><di:waypoint x="860" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_5_di" bpmnElement="Flow_5">
        <di:waypoint x="980" y="320" /><di:waypoint x="1010" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_6_di" bpmnElement="Flow_6">
        <di:waypoint x="1130" y="320" /><di:waypoint x="1160" y="320" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_7_di" bpmnElement="Flow_7">
        <di:waypoint x="1215" y="360" /><di:waypoint x="1215" y="465" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_Recoleta_di" bpmnElement="Flow_Recoleta">
        <di:waypoint x="1190" y="490" /><di:waypoint x="1170" y="490" /><di:waypoint x="1170" y="360" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_Adequada_di" bpmnElement="Flow_Adequada">
        <di:waypoint x="1240" y="490" /><di:waypoint x="1270" y="490" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_8_di" bpmnElement="Flow_8">
        <di:waypoint x="1380" y="490" /><di:waypoint x="1400" y="490" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_9_di" bpmnElement="Flow_9">
        <di:waypoint x="1450" y="450" /><di:waypoint x="1450" y="406" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_10_di" bpmnElement="Flow_10">
        <di:waypoint x="1450" y="530" /><di:waypoint x="1450" y="620" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_11_di" bpmnElement="Flow_11">
        <di:waypoint x="1450" y="700" /><di:waypoint x="1450" y="730" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


def main():
    DIR_BPMN.mkdir(parents=True, exist_ok=True)
    arquivo = DIR_BPMN / "processo_atual.bpmn"
    arquivo.write_text(gerar_processo_atual(), encoding="utf-8")
    print(f"✓ BPMN gerado com sucesso em: {arquivo}")


if __name__ == "__main__":
    main()