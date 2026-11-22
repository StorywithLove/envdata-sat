const DATA_URL = "./data/latest.json";
const map = L.map("map").setView([35.8, 104.2], 4);
// Keep the tile provider's credit, drop Leaflet's own badge from the corner.
map.attributionControl.setPrefix(false);

// Base map: Tianditu renders China's official boundaries (OpenStreetMap does
// not — it draws South Tibet as India). The browser key below is public by
// design; it is protected by the domain allow-list on the Tianditu console.
const TIANDITU_KEY = "2fe2d9b834d1cd9d9526fe9236dfe17a";
const AMAP_TILE =
  "https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}";

function tiandituLayer(layer) {
  return L.tileLayer(
    `https://t{s}.tianditu.gov.cn/${layer}_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0` +
    `&LAYER=${layer}&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles` +
    `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${TIANDITU_KEY}`,
    { subdomains: "01234567", maxZoom: 18, attribution: "&copy; 天地图" }
  );
}

// Tianditu's vector base only covers China; its imagery covers the whole
// world. Both carry the official Chinese boundary, so the imagery layer is the
// one to reach for when looking outside China.
const vectorBase = L.layerGroup([tiandituLayer("vec"), tiandituLayer("cva")]);
const imageBase = L.layerGroup([tiandituLayer("img"), tiandituLayer("cia")]);
const terrainBase = L.layerGroup([tiandituLayer("ter"), tiandituLayer("cta")]);
const amapBase = L.tileLayer(AMAP_TILE, {
  subdomains: "1234", maxZoom: 18, attribution: "&copy; 高德地图",
});
// Terrain is the default: radiation correlates strongly with relief.
terrainBase.addTo(map);
L.control.layers(
  { "天地图·地形": terrainBase, "天地图·矢量": vectorBase, "天地图·影像": imageBase, "高德地图": amapBase },
  null, { position: "topleft" },
).addTo(map);
// A disabled or over-quota key must not leave the page with a blank map.
let baseFellBack = false;
terrainBase.eachLayer(layer => layer.on("tileerror", () => {
  if (baseFellBack) return;
  baseFellBack = true;
  map.removeLayer(terrainBase);
  amapBase.addTo(map);
}));
let chart;
const markers = new Map();
let selectedCity = null;
const fmt = value => value == null ? "缺测" : `${Math.round(value)} W/m²`;
const fmtTime = iso => iso ? new Date(iso).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "未知";
let windowEnd = null;
let lagHours = null;
function color(value) {
  if (value == null) return "#94a3b8";
  if (value < 150) return "#2563eb";
  if (value < 400) return "#16a34a";
  if (value < 700) return "#f59e0b";
  return "#dc2626";
}
function selectCity(city, rows) {
  selectedCity = city.city_id;
  document.getElementById("selected-title").textContent = `${city.city} · 最近24小时`;
  document.getElementById("selected-meta").textContent =
    `来源：${city.source}；质量标记：${city.quality_flag}；时间：${city.time_utc}` +
    (windowEnd ? `；数据截至 ${fmtTime(windowEnd)}（滞后 ${lagHours == null ? "未知" : lagHours.toFixed(1)} 小时）` : "");
  document.querySelectorAll(".city").forEach(el => el.classList.toggle("active", el.dataset.id === selectedCity));
  const points = rows.filter(r => r.city_id === selectedCity).sort((a,b)=>a.time_utc.localeCompare(b.time_utc));
  const labels = points.map(r => new Date(r.time_utc).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Shanghai"}));
  const values = points.map(r => r.ghi_wm2);
  if (typeof Chart === "undefined") {
    // CDN blocked: keep the map/list usable instead of aborting the whole render.
    document.getElementById("selected-meta").textContent += "；图表库加载失败，仅显示最新值。";
    return;
  }
  if (chart) chart.destroy();
  chart = new Chart(document.getElementById("chart"), {
    type:"line",
    data:{labels,datasets:[{label:"GHI (W/m²)",data:values,borderColor:"#2563eb",backgroundColor:"rgba(37,99,235,.12)",spanGaps:false,tension:.2,pointRadius:0,fill:true}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:8}},y:{beginAtZero:true,title:{display:true,text:"W/m²"}}}}
  });
}
function render(payload) {
  const rows = payload.records || [];
  const stamp = payload.generated_at_utc ? new Date(payload.generated_at_utc).toLocaleString("zh-CN") : "未知";
  windowEnd = payload.window_end_utc || null;
  lagHours = payload.data_lag_hours == null ? null : Number(payload.data_lag_hours);
  const windowText = payload.window_start_utc && payload.window_end_utc
    ? `窗口：${fmtTime(payload.window_start_utc)} → ${fmtTime(payload.window_end_utc)}（数据滞后 ${lagHours == null ? "未知" : lagHours.toFixed(1)} 小时）；`
    : "";
  const banner = document.getElementById("banner");

  // Upstream can stop publishing for days (every point comes back null). The
  // payload is then valid but empty, and rendering it leaves a blank map that
  // reads as "the site is broken". Say what actually happened instead.
  if (!rows.length) {
    if (banner) {
      banner.hidden = false;
      banner.textContent =
        "数据源暂停：上游卫星辐射接口在这个窗口内只返回空值，当前没有可展示的观测。" +
        "这不是本站故障——采集任务仍在每小时运行，上游恢复发布后本页会自动更新，无需人工干预。";
    }
    document.getElementById("selected-title").textContent = "暂无有效观测";
    document.getElementById("selected-meta").textContent = `快照更新时间：${stamp}。`;
    document.getElementById("status").textContent = `${windowText}快照更新时间：${stamp}；有效城市：0 / 31；数据点：0。`;
    return;
  }
  if (banner) {
    // A lag over a day means the provider stopped publishing; the chart below is
    // the most recent data that exists, not today's. Say so instead of letting
    // the timestamp speak for itself.
    if (lagHours != null && lagHours > 24) {
      banner.hidden = false;
      banner.textContent =
        `上游卫星辐射接口已约 ${Math.round(lagHours)} 小时未发布新数据，当前展示的是最近一次有效数据（截至 ${fmtTime(windowEnd)}）。` +
        "采集任务仍在每小时运行，上游恢复后本页会自动更新，无需人工干预。";
    } else {
      banner.hidden = true;
    }
  }

  const latest = new Map();
  for (const row of rows) {
    if (row.ghi_wm2 == null) continue;
    const old = latest.get(row.city_id);
    if (!old || row.time_utc > old.time_utc) latest.set(row.city_id,row);
  }
  for (const city of latest.values()) {
    const marker = L.circleMarker([city.latitude,city.longitude],{
      radius:8,weight:1,color:"#fff",fillColor:color(city.ghi_wm2),fillOpacity:.9
    }).addTo(map);
    marker.bindTooltip(`${city.city}：${fmt(city.ghi_wm2)}`);
    marker.on("click",()=>selectCity(city,rows));
    markers.set(city.city_id,marker);
  }
  const list = document.getElementById("city-list");
  list.innerHTML = "";
  [...latest.values()].sort((a,b)=>a.city.localeCompare(b.city,"zh-CN")).forEach(city=>{
    const button=document.createElement("button");
    button.className="city";button.dataset.id=city.city_id;
    const name=document.createElement("strong");name.textContent=city.city;
    const value=document.createElement("span");value.className="value";value.textContent=fmt(city.ghi_wm2);
    button.append(name,value);button.addEventListener("click",()=>{map.setView([city.latitude,city.longitude],6);selectCity(city,rows);});
    list.append(button);
  });
  document.getElementById("status").textContent=`${windowText}快照更新时间：${stamp}；有效城市：${latest.size} / 31；数据点：${rows.length}`;
  const first=latest.get("wuhan") || latest.values().next().value;
  if(first) selectCity(first,rows);
}
fetch(DATA_URL,{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
  .then(render).catch(err=>{document.getElementById("status").textContent=`数据加载失败：${err.message}。请先运行采集工作流并确认 main 分支已有数据快照。`;});
