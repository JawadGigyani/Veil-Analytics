export function sequentialComposition(epsilons:number[]){return epsilons.reduce((sum,value)=>sum+value,0)}
export function advancedComposition(epsilons:number[],deltaPrime=1e-6){
  if(!epsilons.length)return 0;
  const squares=epsilons.reduce((sum,value)=>sum+value*value,0);
  const correction=epsilons.reduce((sum,value)=>sum+value*Math.expm1(value),0);
  return Math.sqrt(2*Math.log(1/deltaPrime)*squares)+correction;
}
