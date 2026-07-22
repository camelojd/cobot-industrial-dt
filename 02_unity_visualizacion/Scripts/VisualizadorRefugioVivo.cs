/*
 * VisualizadorRefugioVivo.cs
 * Proyecto RefugioVivo, gemelo digital de diseno de refugios modulares.
 *
 * Recibe por MQTT el diseno del santuario (un JSON con la lista de
 * refugios, sus dimensiones, materiales, curva de temperatura interior de
 * 24 horas, presupuesto y alerta) y lo dibuja en Unity: el lote, una caja
 * 3D por refugio coloreada segun su alerta, y un panel con los datos.
 *
 * Sigue el patron M2MqttUnity de los demas gemelos del portafolio.
 * Topico: solarpunk/refugio-vivo/estado
 * Para probar comandos publico en solarpunk/refugio-vivo/cmd, por ejemplo
 * {"cmd":"mas_llantas"} o {"animales":{"gallinas":30}}.
 */

using System;
using System.Text;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using M2MqttUnity;

[Serializable]
public class RefugioData
{
    public string id;
    public string especie;
    public int n_animales;
    public string color;
    public float largo;
    public float ancho;
    public float alto;
    public float pos_x;
    public float pos_z;
    public string material_techo;
    public string materiales_desc;
    public float temp_min;
    public float temp_max;
    public float[] temp_curva;
    public float rango_ideal_min;
    public float rango_ideal_max;
    public int horas_fuera_rango;
    public bool estructura_ok;
    public long costo;
    public string alerta;
}

[Serializable]
public class EstadoRefugio
{
    public bool diseno_viable;
    public string ciudad;
    public float lote_ancho;
    public float lote_largo;
    public float area_usada;
    public float area_disponible;
    public string[] especies_alojadas;
    public RefugioData[] refugios;
    public long costo_total;
    public long presupuesto_max;
    public int semanas_construccion;
    public string alerta_global;
    public string fecha;
}

public class VisualizadorRefugioVivo : M2MqttUnityClient
{
    [Header("Topico")]
    public string topicEstado = "solarpunk/refugio-vivo/estado";

    [Header("Escena")]
    public Transform contenedorRefugios;   // padre vacio donde se instancian los refugios
    public Transform lote;                  // plano del terreno (opcional)

    [Header("UI")]
    public Text panelDatos;                 // tabla de refugios y totales
    public LineRenderer curvaInterior;      // curva de temperatura interior del primer refugio
    public LineRenderer curvaExterior;      // opcional, referencia visual

    [Header("Colores de alerta")]
    public Color verde = new Color(0.18f, 0.80f, 0.44f);
    public Color amarillo = new Color(0.95f, 0.77f, 0.25f);
    public Color rojo = new Color(0.90f, 0.30f, 0.24f);

    private readonly List<GameObject> refugiosEnEscena = new List<GameObject>();

    protected override void OnConnected()
    {
        base.OnConnected();
        Debug.Log("RefugioVivo conectado al broker MQTT.");
    }

    protected override void SubscribeTopics()
    {
        client.Subscribe(new string[] { topicEstado },
            new byte[] { MqttMsgBase.QOS_LEVEL_AT_MOST_ONCE });
    }

    protected override void UnsubscribeTopics()
    {
        client.Unsubscribe(new string[] { topicEstado });
    }

    protected override void DecodeMessage(string topic, byte[] message)
    {
        string json = Encoding.UTF8.GetString(message);
        EstadoRefugio estado;
        try
        {
            estado = JsonUtility.FromJson<EstadoRefugio>(json);
        }
        catch (Exception e)
        {
            Debug.LogWarning("No pude leer el diseno: " + e.Message);
            return;
        }
        if (estado == null || estado.refugios == null) return;

        ConstruirEscena(estado);
        ActualizarPanel(estado);
        DibujarCurva(estado);
    }

    private void ConstruirEscena(EstadoRefugio estado)
    {
        foreach (GameObject go in refugiosEnEscena)
        {
            if (go != null) Destroy(go);
        }
        refugiosEnEscena.Clear();

        if (lote != null)
        {
            lote.localScale = new Vector3(estado.lote_ancho, 1f, estado.lote_largo);
        }

        foreach (RefugioData r in estado.refugios)
        {
            GameObject caja = GameObject.CreatePrimitive(PrimitiveType.Cube);
            caja.name = r.id + " (" + r.especie + ")";
            if (contenedorRefugios != null) caja.transform.SetParent(contenedorRefugios, false);
            caja.transform.localScale = new Vector3(r.largo, r.alto, r.ancho);
            caja.transform.localPosition = new Vector3(r.pos_x, r.alto * 0.5f, r.pos_z);

            Renderer rend = caja.GetComponent<Renderer>();
            rend.material.color = ColorDeAlerta(r.alerta);
            refugiosEnEscena.Add(caja);
        }
    }

    private void ActualizarPanel(EstadoRefugio estado)
    {
        if (panelDatos == null) return;
        StringBuilder sb = new StringBuilder();
        string estadoTxt = estado.diseno_viable ? "DISENO VIABLE" : "DISENO NO VIABLE";
        sb.AppendLine(estadoTxt + " (" + estado.alerta_global.ToUpper() + ")");
        sb.AppendLine("Ciudad: " + estado.ciudad + " | Lote: " + estado.lote_ancho + " x " + estado.lote_largo + " m");
        sb.AppendLine("Area usada: " + estado.area_usada + " / " + estado.area_disponible + " m2");
        sb.AppendLine("Costo: $" + estado.costo_total.ToString("N0") + " / $" + estado.presupuesto_max.ToString("N0") + " COP");
        sb.AppendLine("Construccion: " + estado.semanas_construccion + " semanas");
        sb.AppendLine("");
        foreach (RefugioData r in estado.refugios)
        {
            sb.AppendLine(r.id + " | " + r.especie + " x" + r.n_animales
                + " | " + r.largo + "x" + r.ancho + "x" + r.alto + " m"
                + " | techo " + r.material_techo
                + " | T " + r.temp_min + "-" + r.temp_max + " C"
                + " | $" + r.costo.ToString("N0")
                + " | " + r.alerta.ToUpper());
            sb.AppendLine("   " + r.materiales_desc);
        }
        panelDatos.text = sb.ToString();
    }

    private void DibujarCurva(EstadoRefugio estado)
    {
        if (curvaInterior == null || estado.refugios.Length == 0) return;
        float[] curva = estado.refugios[0].temp_curva;
        if (curva == null || curva.Length == 0) return;

        curvaInterior.positionCount = curva.Length;
        float ancho = 6f;             // ancho del grafico en unidades de mundo
        float alto = 3f;              // alto del grafico
        float tMin = 0f, tMax = 40f;  // escala de temperatura del eje Y
        for (int i = 0; i < curva.Length; i++)
        {
            float x = (i / (float)(curva.Length - 1)) * ancho;
            float y = Mathf.InverseLerp(tMin, tMax, curva[i]) * alto;
            curvaInterior.SetPosition(i, new Vector3(x, y, 0f));
        }
    }

    private Color ColorDeAlerta(string alerta)
    {
        switch (alerta)
        {
            case "verde": return verde;
            case "amarillo": return amarillo;
            case "rojo": return rojo;
            default: return Color.gray;
        }
    }
}
