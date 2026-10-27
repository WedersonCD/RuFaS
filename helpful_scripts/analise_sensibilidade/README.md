# Sensibilidade OAT dos preços — GO_campo_dourado

Na raiz do repositório, com o ambiente Python do RuFaS ativado:

```bash
python helpful_scripts/analise_sensibilidade/run_oat.py
```

O script lê `GO_campo_dourado/_pipeline_task_manager_metadata.json`, segue os
metadados até o arquivo de alimentos efetivamente utilizado e executa uma
simulação completa por cenário. Cada cenário parte dos preços originais e altera
somente `purchased_feed_cost` de um `feed_type`, multiplicando-o por 0,8 ou 1,2.
Nenhum arquivo original da fazenda é editado.

Por padrão, há até **3 simulações simultâneas**, cada uma em um processo Python
independente, usando o mesmo ambiente que executou o script. Os nove ingredientes
atuais geram 18 cenários, mais uma baseline sem alteração: **19 simulações**.
A semente é fixa e comum a todos os cenários (44 na configuração atual), passada
no campo `random_seeds`, que é o campo lido pelo TaskManager desta versão.

Os resultados ficam em uma nova pasta `output/analise_sensibilidade/<timestamp>/`:

```text
go_campo_dourado_baseline.csv
go_campo_dourado_51_minus_20.csv
go_campo_dourado_51_plus_20.csv
...
manifest.json
scenarios/
```

Cada CSV contém todas as variáveis exportáveis pelo OutputManager, em formato
portrait, geradas por um filtro próprio. Não depende dos filtros existentes em
`output/output_filters`. Os arquivos originais, logs e configurações de cada
execução ficam em `scenarios/<nome>/`. O manifesto registra preços, semente,
status e erros. O CSV com o nome final só é copiado após confirmar a conclusão
da simulação e a presença de exatamente um CSV não vazio. Falhas de um cenário
não interrompem os demais; ao final, o script retorna código 1 se houver falhas.

Opções:

```bash
# Preparar e conferir os 19 cenários sem executar simulações
python helpful_scripts/analise_sensibilidade/run_oat.py --dry-run

# Rodar apenas um ingrediente, com baseline
python helpful_scripts/analise_sensibilidade/run_oat.py --feed-types 51

# Reduzir uso simultâneo de memória
python helpful_scripts/analise_sensibilidade/run_oat.py --workers 1

# Pasta de destino explícita (deve ser nova)
python helpful_scripts/analise_sensibilidade/run_oat.py --output-dir output/meu_experimento

# Omitir baseline, ou escolher outra semente fixa
python helpful_scripts/analise_sensibilidade/run_oat.py --skip-baseline --seed 44
```

Três simulações completas podem consumir bastante memória. Ajuste `--workers`
conforme a máquina. As bibliotecas numéricas recebem um limite de uma thread
por processo para evitar paralelismo adicional. Não altere código ou entradas
da fazenda durante o experimento: somente os arquivos que precisam mudar são
copiados, e os demais inputs são lidos do repositório.

Esta é uma análise do simulador completo, com seus efeitos ao longo do tempo,
e não apenas do otimizador isolado. O script não troca o otimizador configurado.
Ingredientes com preço zero continuam com preço zero; ingredientes sem uso podem
não produzir efeito. Compare os resultados com a baseline do mesmo experimento.

Testes do executor (biblioteca padrão, sem dependências do simulador):

```bash
python -m unittest discover -s helpful_scripts/analise_sensibilidade -p 'test_*.py' -v
```

Esses testes usam um processo substituto para conferir isolamento, exportação,
controle de concorrência e detecção de falhas; não validam resultados biológicos.
