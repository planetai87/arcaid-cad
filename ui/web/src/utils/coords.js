// archsketch의 coords.js를 참고한 mm 좌표 ↔ 화면 좌표 변환(Y축 위가 +).
export const screenPoint = ([x,y]) => [x,-y];
export function fromScreen(point, view) {
  return {x:(point.x-view.x)/view.scale, y:-(point.y-view.y)/view.scale};
}
