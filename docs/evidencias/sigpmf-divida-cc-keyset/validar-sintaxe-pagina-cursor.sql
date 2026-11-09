-- Validação somente leitura da página COM cursor de dbo.Divida_Conta_Corrente.
-- Valores sintéticos (não são dados de produção). Não cria índice, não altera dados nem timeout.
-- O texto da consulta é o que o conector gera, com literais no lugar de %s.
-- Não foi executado neste repositório: não há SQL Server aqui.
--
-- 1) Sintaxe, sem compilar e sem executar:
-- 2) Plano estimado. SET SHOWPLAN_XML precisa ser o único comando do lote (GO).
--    Compilar o plano lê estatísticas; não executa a consulta. Pode demorar na tabela real.

SET PARSEONLY ON;
SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM ((SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] = N'A' AND [SEQU] = N'00' AND [ANO] > 2020 ORDER BY [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] = N'A' AND [SEQU] > N'00' ORDER BY [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] > N'A' ORDER BY [CODI], [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] > 1 ORDER BY [Sequencia], [CODI], [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] > '2024-01-01T00:00:00' ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO])) AS [pagina] ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO];
SET PARSEONLY OFF;
GO

SET SHOWPLAN_XML ON;
GO
SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM ((SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] = N'A' AND [SEQU] = N'00' AND [ANO] > 2020 ORDER BY [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] = N'A' AND [SEQU] > N'00' ORDER BY [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] = 1 AND [CODI] > N'A' ORDER BY [CODI], [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] = '2024-01-01T00:00:00' AND [Sequencia] > 1 ORDER BY [Sequencia], [CODI], [SEQU], [ANO]) UNION ALL (SELECT TOP (1000) [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO], [Vr_Pago], [Compo] FROM [dbo].[Divida_Conta_Corrente] WHERE [Data_Movimentacao] > '2024-01-01T00:00:00' ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO])) AS [pagina] ORDER BY [Data_Movimentacao], [Sequencia], [CODI], [SEQU], [ANO];
GO
SET SHOWPLAN_XML OFF;
GO
