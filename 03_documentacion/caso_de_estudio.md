# Caso de Estudio: Gemelo Digital de Diseno de Refugios Modulares con Materiales Reciclados

**Proyecto:** RefugioVivo
**Dominio:** Industria Viva (refugios para animales rescatados, economia circular)

---

## Desafio

Los santuarios de animales rescatados en Colombia rara vez tienen presupuesto para comprar refugios prefabricados, y sin embargo hay materiales reciclados de sobra que podrian servir: contenedores en desuso, madera de demolicion, guadua, llantas, adobe. El problema es que aprovecharlos bien no es intuitivo. Cada especie necesita un rango de temperatura, cierta ventilacion y cierto espacio minimo. Un techo de contenedor de acero convierte el refugio en un horno al mediodia, mientras uno de guadua mantiene el interior estable. Y todo tiene que caber en el lote, resistir la lluvia de la region y entrar en un presupuesto muy ajustado. Sin una herramienta que calcule todo eso junto, las decisiones se toman a ojo, y un error sale caro cuando ya se construyo.

## Solucion

Lo que arme fue un **gemelo digital** que toma el lote, el clima real de la zona, el inventario de materiales reciclados y las especies a alojar, y propone un diseno completo antes de construir:

- Escribi un **simulador en Python** que, para cada especie, calcula el espacio necesario y unas dimensiones optimas de refugio, elige el mejor material de techo disponible en el inventario, corre un modelo termico de 24 horas (temperatura interior hora a hora segun el material, la radiacion solar y el calor de los animales), verifica que la estructura resista, arma el presupuesto en pesos y estima el cronograma. Publica todo ese diseno como un JSON al topico MQTT `solarpunk/refugio-vivo/estado`.
- Del otro lado, una escena de **Unity** (con el cliente M2MqttUnity) dibuja el lote y una caja 3D por refugio, coloreada segun su alerta: **verde** (diseno viable), **amarillo** (hay restricciones, como horas fuera del rango ideal de la especie o falta de material en el inventario) y **rojo** (no cabe en el lote o el techo no resiste). Un panel muestra las dimensiones, la curva de temperatura, el presupuesto y el cronograma.
- Tambien deje un topico `solarpunk/refugio-vivo/cmd` para cambiar el diseno en vivo: mas animales de una especie, otra ciudad (recalcula con otro clima), o ajustar el inventario ("menos madera, mas llantas"). El simulador recalcula al instante.

## Tecnologias

- **Python** (paho-mqtt) con calculo termico y optimizacion parametrica en la libreria estandar
- **Unity** (C#, uGUI) con **M2MqttUnity**
- **MQTT** (broker `broker.emqx.io`) con mensajes **JSON**

## Resultados

- Consegui comparar disenos de un vistazo: cambiar el material del techo o la ciudad y ver de inmediato como se mueve la curva de temperatura interior y si la especie queda comoda o no.
- El presupuesto y el cronograma salen con precios reales de materiales reciclados en Colombia, asi que el numero sirve para gestionar donaciones o compras, no es un adorno.
- Las alertas por refugio (verde, amarillo, rojo) convierten un problema de diseno complejo en una decision clara: este diseno es viable, este tiene un problema de confort, este no cabe.
- La deje preparada para crecer: clima real por API segun coordenadas, optimizacion de distribucion en el lote buscando sombra y ventilacion, y exportar el diseno como plano y lista de compras.

**Aclaracion:** este es un proyecto de simulacion con fines de portafolio y aprendizaje. La curva de temperatura es un modelo simplificado (no es CFD real), pero es util para comparar disenos entre si. Los precios y datos climaticos son reales y aproximados.
