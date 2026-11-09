cwlVersion: v1.2
class: CommandLineTool
label: cat-file
doc: read a File input (paths differ per implementation, so only the output content is compared)
baseCommand: cat
inputs:
  infile:
    type: File
    inputBinding: {position: 1}
outputs:
  out:
    type: File
    outputBinding: {glob: copy.txt}
stdout: copy.txt
requirements:
  DockerRequirement: {dockerPull: "alpine:3.20"}
