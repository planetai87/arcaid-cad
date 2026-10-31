// archsketch의 viewport.js·PlanCanvas 휠 기준점 보존 방식을 참고했다.
export function fitView(bounds, size, pad=55) {
  const scale = Math.max(0.0001, Math.min((size.width-pad*2)/Math.max(1,bounds.maxX-bounds.minX),
    (size.height-pad*2)/Math.max(1,bounds.maxY-bounds.minY)));
  return {scale,x:size.width/2-(bounds.minX+bounds.maxX)/2*scale,
    y:size.height/2+(bounds.minY+bounds.maxY)/2*scale};
}
export function zoomAt(view, point, factor) {
  const scale = Math.max(0.0001,Math.min(100,view.scale*factor));
  return {scale,x:point.x-(point.x-view.x)*scale/view.scale,
    y:point.y-(point.y-view.y)*scale/view.scale};
}
