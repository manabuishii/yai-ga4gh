cwlVersion: v1.2
class: CommandLineTool
label: args-order
doc: argv order (position, prefix, arguments; at equal position arguments come first; inputs by name)
baseCommand: [echo, start]
arguments: [arg-zero]
inputs:
  t:
    type: string
    inputBinding: {position: 2}
  s:
    type: string
    inputBinding: {position: 2, prefix: --s}
  n:
    type: int
    inputBinding: {position: 1, prefix: -n}
  unbound:
    type: string
    default: not-on-the-command-line
outputs:
  out:
    type: File
    outputBinding: {glob: out.txt}
stdout: out.txt
requirements:
  DockerRequirement: {dockerPull: "alpine:3.20"}
