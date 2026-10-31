// archsketch의 snap.js에서 가져온 좌표 반올림(이 뷰어는 객체를 이동하지 않는다).
export function snapToGrid(valueMm, gridMm = 1) {
  return Math.round(valueMm / gridMm) * gridMm;
}
