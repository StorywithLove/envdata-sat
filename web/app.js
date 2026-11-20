const DATA_URL = "https://raw.githubusercontent.com/StorywithLove/envdata-sat/main/data/cities/latest.json";
const map = L.map("map").setView([35.8, 104.2], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 10, attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);
let chart;
const markers = new Map();
let selectedCity = null;
const fmt = value => value == null ? "缺测" : `${Math.round(value)} W/m²`;
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
    `来源：${city.source}；质量标记：${city.quality_flag}；时间：${city.time_utc}`;
  document.querySelectorAll(".city").forEach(el => el.classList.toggle("active", el.dataset.id === selectedCity));
  const points = rows.filter(r => r.city_id === selectedCity).sort((a,b)=>a.time_utc.localeCompare(b.time_utc));
  const labels = points.map(r => new Date(r.time_utc).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Shanghai"}));
  const values = points.map(r => r.ghi_wm2);
  if (chart) chart.destroy();
  chart = new Chart(document.getElementById("chart"), {
    type:"line",
    data:{labels,datasets:[{label:"GHI (W/m²)",data:values,borderColor:"#2563eb",backgroundColor:"rgba(37,99,235,.12)",spanGaps:false,tension:.2,pointRadius:0,fill:true}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:8}},y:{beginAtZero:true,title:{display:true,text:"W/m²"}}}}
  });
}
function render(payload) {
  const rows = payload.records || [];
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
  const stamp=payload.generated_at_utc ? new Date(payload.generated_at_utc).toLocaleString("zh-CN") : "未知";
  document.getElementById("status").textContent=`快照更新时间：${stamp}；有效城市：${latest.size} / 31；数据点：${rows.length}`;
  const first=latest.get("wuhan") || latest.values().next().value;
  if(first) selectCity(first,rows);
}
fetch(DATA_URL,{cache:"no-store"}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();})
  .then(render).catch(err=>{document.getElementById("status").textContent=`数据加载失败：${err.message}。请先运行采集工作流并确认 main 分支已有数据快照。`;});
