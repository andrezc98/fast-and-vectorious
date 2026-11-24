"""Write architecture.drawio: the lab setup in AWS reference-architecture style.

    python3 article/img/src/architecture.py
    /Applications/draw.io.app/Contents/MacOS/draw.io -x -f png --width 1600 -b 20 \
        -o article/img/01-architecture.png article/img/src/architecture.drawio

Topology follows infra/network.tf, infra/stores.tf and infra/runner.tf.
"""
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "fontFamily=Helvetica;"
INK = "#232F3E"
PTS = ("points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],[1,1],"
       "[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];")
cells = []


def cell(id_, value, style, x, y, w, h):
    value = value.replace("\n", "<br>")
    cells.append(f'<mxCell id="{id_}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" '
                 f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def group(id_, label, icon, stroke, fill, x, y, w, h, *, dashed=0, bold=0):
    cell(id_, label, f"{PTS}outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=13;"
         f"fontStyle={bold};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.{icon};strokeColor={stroke};"
         f"fillColor={fill};verticalAlign=top;align=left;spacingLeft=30;fontColor={stroke};dashed={dashed};"
         f"container=0;pointerEvents=0;collapsible=0;recursiveResize=0;{FONT}", x, y, w, h)


def service(id_, label, icon, color, x, y, size=64):
    cell(id_, label, f"sketch=0;outlineConnect=0;fontColor={INK};gradientColor=none;fillColor={color};"
         f"strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;"
         f"fontSize=12;fontStyle=0;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};{FONT}",
         x, y, size, size)


def resource(id_, label, shape, color, x, y, size=48, label_pos="bottom"):
    pos = ("verticalLabelPosition=bottom;verticalAlign=top;align=center;" if label_pos == "bottom" else
           "labelPosition=left;verticalLabelPosition=middle;align=right;verticalAlign=middle;spacingRight=4;")
    cell(id_, label, f"sketch=0;outlineConnect=0;fontColor={INK};gradientColor=none;fillColor={color};"
         f"strokeColor=none;dashed=0;{pos}html=1;fontSize=12;fontStyle=0;aspect=fixed;pointerEvents=1;"
         f"shape=mxgraph.aws4.{shape};labelBackgroundColor=#ffffff;{FONT}", x, y, size, size)


def edge(id_, src, dst, exit_, entry, points=(), dashed=0):
    wp = "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points)
    geo = f'<Array as="points">{wp}</Array>' if wp else ""
    cells.append(f'<mxCell id="{id_}" style="edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;endFill=1;'
                 f'strokeColor=#545B64;strokeWidth=1.5;rounded=0;dashed={dashed};exitX={exit_[0]};exitY={exit_[1]};'
                 f'exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;{FONT}" '
                 f'edge="1" parent="1" source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry">'
                 f'{geo}</mxGeometry></mxCell>')


def badge(n, x, y):
    cell(f"badge-{n}", str(n), f"ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor={INK};strokeColor=none;"
         f"fontColor=#FFFFFF;fontStyle=1;fontSize=13;{FONT}", x, y, 26, 26)


# Groups
group("aws-cloud", "AWS Cloud", "group_aws_cloud_alt", INK, "none", 20, 20, 1300, 700)
group("region", "us-east-1", "group_region", "#00A4A6", "none", 40, 60, 1260, 640, dashed=1)
group("vpc", "VPC", "group_vpc2", "#8C4FFF", "none", 70, 110, 790, 540)
group("private", "Private subnets (two Availability Zones)", "group_private_subnet", "#147EBA", "#E6F2F8",
      100, 160, 300, 460)
group("public", "Public subnet", "group_public_subnet", "#248814", "#F2F6E8", 440, 160, 380, 460)

# Inside the VPC
service("aurora", "Aurora PostgreSQL\nServerless v2\n<i>pgvector 0.8.2</i>", "aurora", "#C925D1", 218, 360)
resource("runner", "EC2 m7g.2xlarge runner\n<i>Python, boto3, NumPy</i>", "instance2", "#ED7100", 568, 364, 56)
resource("ifep", "Interface endpoints\n<i>S3 Vectors, OpenSearch</i>", "endpoints", "#8C4FFF", 712, 500)

# On the VPC edge
resource("igw", "Internet\ngateway", "internet_gateway", "#8C4FFF", 836, 164)
resource("gwep", "Gateway endpoint\n<i>DynamoDB</i>", "endpoints", "#8C4FFF", 836, 316)

# Regional services
service("bedrock", "Amazon Bedrock\n<i>Cohere Embed v4</i>", "bedrock", "#01A88D", 1060, 156)
service("ddb", "Amazon DynamoDB\n<i>vector index</i>", "dynamodb", "#C925D1", 1060, 308)
service("s3v", "Amazon S3 Vectors\n<i>vector bucket and index</i>", "s3", "#7AA116", 1060, 440)
service("aoss", "Amazon OpenSearch\nServerless\n<i>k-NN collection</i>", "elasticsearch_service", "#8C4FFF",
        1060, 560)

# Flows
edge("e-embed-1", "runner", "igw", (0.5, 0), (0, 0.5), points=[(596, 188)])
edge("e-embed-2", "igw", "bedrock", (1, 0.5), (0, 0.5))
edge("e-ddb-1", "runner", "gwep", (1, 0.25), (0, 0.5), points=[(680, 378), (680, 340)])
edge("e-ddb-2", "gwep", "ddb", (1, 0.5), (0, 0.5))
edge("e-if", "runner", "ifep", (1, 0.75), (0.5, 0), points=[(736, 406)])
edge("e-s3v", "ifep", "s3v", (1, 0.25), (0, 0.5), points=[(980, 512), (980, 472)])
edge("e-aoss", "ifep", "aoss", (1, 0.75), (0, 0.5), points=[(980, 536), (980, 592)])
edge("e-aurora", "runner", "aurora", (0, 0.5), (1, 0.5))

# Steps
badge(1, 560, 260)
badge(2, 534, 336)
badge(3, 950, 300)
badge(4, 900, 478)
badge(5, 430, 400)
badge(6, 583, 468)

# Legend under the diagram
steps = [
    "The runner embeds the corpus and the test questions once with Cohere Embed v4 on Amazon Bedrock.",
    "It loads the same 1,024-dimension vectors into all four stores.",
    "DynamoDB SearchVectors calls route through the gateway endpoint.",
    "S3 Vectors and OpenSearch Serverless calls go through interface endpoints (AWS PrivateLink).",
    "Aurora is queried with SQL inside the VPC.",
    "Every answer is scored against exact NumPy search and written as one CSV row.",
]
for i, text in enumerate(steps):
    col, row = divmod(i, 3)
    x, y = 30 + col * 650, 745 + row * 36
    badge(f"{i + 1}-legend", x, y)
    cells[-1] = cells[-1].replace(f'value="{i + 1}-legend"', f'value="{i + 1}"')
    cell(f"legend-{i + 1}", text, f"text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;fontSize=12;"
         f"fontColor={INK};{FONT}", x + 34, y - 4, 600, 34)

xml = ('<mxfile host="Electron"><diagram name="Lab setup" id="lab-setup"><mxGraphModel dx="1400" dy="1000" '
       'grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" '
       'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
Path(__file__).with_name("architecture.drawio").write_text(xml, encoding="utf-8")
print("wrote architecture.drawio")
