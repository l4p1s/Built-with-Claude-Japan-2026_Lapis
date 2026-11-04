// 記入ページ（index.html）と、回答を見るページ（view.html）で共通に使うもの。設計: docs/survey-design.md

// 回答の送り先（Google Apps Script のウェブアプリの URL）。設定のしかたは docs/survey-design.md §8
export const ENDPOINT = "https://script.google.com/macros/s/AKfycbzUitSKkbpA2hOK7SUtZX3DGqB3FGGCPv2DK5_BRs182hzvylbc59iBOIIuf8vJ0mdz/exec";

export const START = { center: [130.6016, 32.5072], zoom: 13 }; // 八代市役所
// 地図は国土地理院の標準地図（学校・病院・神社・町名などの目印が載る）と、航空写真（家の屋根で見つけやすい）。
// どちらも明るい地図だけなので、ダークモードでも地図は明るいまま
const GSI = { map: ["std", "png"], photo: ["seamlessphoto", "jpg"] };
export function gsiStyle(kind = "map") {
  const [id, ext] = GSI[kind];
  return {
    version: 8,
    sources: {
      gsi: {
        type: "raster", tileSize: 256, maxzoom: 18,
        tiles: [`https://cyberjapandata.gsi.go.jp/xyz/${id}/{z}/{x}/{y}.${ext}`],
        attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html">国土地理院</a>',
      },
    },
    layers: [{ id: "gsi", type: "raster", source: "gsi" }],
  };
}
export const ROUTE_COLOR = "#1e3a5f"; // 地図はいつも明るいので、線は濃い紺

// キーは本体の app/rules.py の Kind と重なるものは同じ名前
export const TYPE = {
  home: "自宅", work: "職場・学校", shop: "店", water: "給水", food: "炊き出し",
  shelter: "避難所", car_sleeping: "車中泊", friend: "知人の家", other: "その他",
};
export const HEARD = {
  neighbor: "お隣さん", instagram: "インスタ", line: "LINE", poster: "張り紙",
  passing: "通りがかり", usual: "いつも行く", other: "その他",
};
export const MODE = { walk: "徒歩", car: "車", bicycle: "自転車", other: "その他" };

export const minutes = (t) => (t ? Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5)) : null);
export const clock = (t) => (t ? `${Number(t.slice(0, 2))}:${t.slice(3, 5)}` : "？");

// 夜をまたぐ滞在（出た時刻 < 着いた時刻）は、帯では24時まで
export function span(s) {
  const a = minutes(s.from), b = minutes(s.to);
  if (a == null || b == null) return null;
  return [a, b < a ? 1440 : b];
}
