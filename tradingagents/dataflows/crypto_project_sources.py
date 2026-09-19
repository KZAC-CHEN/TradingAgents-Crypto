"""无需商业密钥的加密项目官方来源登记表。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProjectFeed:
    """项目维护方公开发布的 RSS 或 Atom 订阅。"""

    name: str
    url: str


@dataclass(frozen=True)
class ProjectReference:
    """供用户人工核对的项目官方入口。"""

    label: str
    url: str
    kind: str
    note: str = ""


@dataclass(frozen=True)
class ProjectProfile:
    """某个基础资产对应的名称、自动订阅和人工核对入口。"""

    project_name: str
    feeds: tuple[ProjectFeed, ...]
    references: tuple[ProjectReference, ...]
    coingecko_id: str = ""
    defillama_entity: str = ""
    defillama_entity_type: str = ""


_PROJECT_PROFILES = {
    "BTC": ProjectProfile(
        "Bitcoin",
        (ProjectFeed("Bitcoin Core Releases", "https://github.com/bitcoin/bitcoin/releases.atom"),),
        (
            ProjectReference("Bitcoin 官网", "https://bitcoin.org/", "website"),
            ProjectReference("Bitcoin 开发文档", "https://developer.bitcoin.org/", "docs"),
            ProjectReference("Bitcoin Core 公告", "https://bitcoincore.org/en/blog/", "blog"),
            ProjectReference("Bitcoin Core GitHub", "https://github.com/bitcoin/bitcoin", "github"),
            ProjectReference("Bitcoin 改进提案", "https://github.com/bitcoin/bips", "governance"),
        ),
        coingecko_id="bitcoin",
        defillama_entity="Bitcoin",
        defillama_entity_type="chain",
    ),
    "ETH": ProjectProfile(
        "Ethereum",
        (ProjectFeed("Geth Releases", "https://github.com/ethereum/go-ethereum/releases.atom"),),
        (
            ProjectReference("Ethereum 官网", "https://ethereum.org/", "website"),
            ProjectReference("Ethereum 开发文档", "https://ethereum.org/developers/docs/", "docs"),
            ProjectReference("Ethereum 基金会博客", "https://blog.ethereum.org/", "blog"),
            ProjectReference("Geth GitHub", "https://github.com/ethereum/go-ethereum", "github"),
            ProjectReference("Ethereum Magicians", "https://ethereum-magicians.org/", "governance"),
        ),
        coingecko_id="ethereum",
        defillama_entity="Ethereum",
        defillama_entity_type="chain",
    ),
    "BNB": ProjectProfile(
        "BNB Chain",
        (ProjectFeed("BNB Smart Chain Releases", "https://github.com/bnb-chain/bsc/releases.atom"),),
        (
            ProjectReference("BNB Chain 官网", "https://www.bnbchain.org/en", "website"),
            ProjectReference("BNB Chain 文档", "https://docs.bnbchain.org/", "docs"),
            ProjectReference("BNB Chain 博客", "https://www.bnbchain.org/en/blog", "blog"),
            ProjectReference("BNB Smart Chain GitHub", "https://github.com/bnb-chain/bsc", "github"),
            ProjectReference("BNB Chain 论坛", "https://forum.bnbchain.org/", "governance"),
        ),
        coingecko_id="binancecoin",
        defillama_entity="BSC",
        defillama_entity_type="chain",
    ),
    "SOL": ProjectProfile(
        "Solana",
        (ProjectFeed("Agave Releases", "https://github.com/anza-xyz/agave/releases.atom"),),
        (
            ProjectReference("Solana 官网", "https://solana.com/", "website"),
            ProjectReference("Solana 文档", "https://solana.com/docs", "docs"),
            ProjectReference("Solana 新闻", "https://solana.com/news", "blog"),
            ProjectReference("Agave GitHub", "https://github.com/anza-xyz/agave", "github"),
            ProjectReference("Solana 论坛", "https://forum.solana.com/", "governance"),
        ),
        coingecko_id="solana",
        defillama_entity="Solana",
        defillama_entity_type="chain",
    ),
    "XRP": ProjectProfile(
        "XRP Ledger",
        (ProjectFeed("XRP Ledger Releases", "https://github.com/XRPLF/rippled/releases.atom"),),
        (
            ProjectReference("XRP Ledger 官网", "https://xrpl.org/", "website"),
            ProjectReference("XRP Ledger 文档", "https://xrpl.org/docs/", "docs"),
            ProjectReference("XRP Ledger 博客", "https://xrpl.org/blog/", "blog"),
            ProjectReference("XRP Ledger GitHub", "https://github.com/XRPLF/rippled", "github"),
        ),
        coingecko_id="ripple",
        defillama_entity="XRPL",
        defillama_entity_type="chain",
    ),
    "DOGE": ProjectProfile(
        "Dogecoin",
        (ProjectFeed("Dogecoin Core Releases", "https://github.com/dogecoin/dogecoin/releases.atom"),),
        (
            ProjectReference("Dogecoin 官网", "https://dogecoin.com/", "website"),
            ProjectReference("Dogecoin 开发文档", "https://dogecoincore.com/", "docs"),
            ProjectReference("Dogecoin Foundation 博客", "https://foundation.dogecoin.com/blog/", "blog"),
            ProjectReference("Dogecoin Core GitHub", "https://github.com/dogecoin/dogecoin", "github"),
        ),
        coingecko_id="dogecoin",
        defillama_entity="Doge",
        defillama_entity_type="chain",
    ),
    "ADA": ProjectProfile(
        "Cardano",
        (ProjectFeed("Cardano Node Releases", "https://github.com/IntersectMBO/cardano-node/releases.atom"),),
        (
            ProjectReference("Cardano 官网", "https://cardano.org/", "website"),
            ProjectReference("Cardano 开发文档", "https://developers.cardano.org/docs/", "docs"),
            ProjectReference("Cardano Foundation 新闻", "https://cardanofoundation.org/en/news/", "blog"),
            ProjectReference("Cardano Node GitHub", "https://github.com/IntersectMBO/cardano-node", "github"),
            ProjectReference("Cardano 论坛", "https://forum.cardano.org/c/governance/140", "governance"),
        ),
        coingecko_id="cardano",
        defillama_entity="Cardano",
        defillama_entity_type="chain",
    ),
    "AVAX": ProjectProfile(
        "Avalanche",
        (ProjectFeed("AvalancheGo Releases", "https://github.com/ava-labs/avalanchego/releases.atom"),),
        (
            ProjectReference("Avalanche 官网", "https://www.avax.network/", "website"),
            ProjectReference("Avalanche Builder Hub", "https://build.avax.network/docs", "docs"),
            ProjectReference("Avalanche 博客", "https://www.avax.network/blog", "blog"),
            ProjectReference("AvalancheGo GitHub", "https://github.com/ava-labs/avalanchego", "github"),
            ProjectReference("Avalanche 改进提案", "https://github.com/avalanche-foundation/ACPs", "governance"),
        ),
        coingecko_id="avalanche-2",
        defillama_entity="Avalanche",
        defillama_entity_type="chain",
    ),
    "LINK": ProjectProfile(
        "Chainlink",
        (ProjectFeed("Chainlink Releases", "https://github.com/smartcontractkit/chainlink/releases.atom"),),
        (
            ProjectReference("Chainlink 官网", "https://chain.link/", "website"),
            ProjectReference("Chainlink 文档", "https://docs.chain.link/", "docs"),
            ProjectReference("Chainlink 博客", "https://blog.chain.link/", "blog"),
            ProjectReference("Chainlink GitHub", "https://github.com/smartcontractkit/chainlink", "github"),
        ),
        coingecko_id="chainlink",
        defillama_entity="chainlink",
        defillama_entity_type="protocol",
    ),
    "DOT": ProjectProfile(
        "Polkadot",
        (ProjectFeed("Polkadot SDK Releases", "https://github.com/paritytech/polkadot-sdk/releases.atom"),),
        (
            ProjectReference("Polkadot 官网", "https://polkadot.com/", "website"),
            ProjectReference("Polkadot Wiki", "https://wiki.polkadot.network/", "docs"),
            ProjectReference("Polkadot 博客", "https://polkadot.com/blog/", "blog"),
            ProjectReference("Polkadot SDK GitHub", "https://github.com/paritytech/polkadot-sdk", "github"),
            ProjectReference("Polkadot OpenGov", "https://polkadot.polkassembly.io/", "governance"),
        ),
        coingecko_id="polkadot",
    ),
}


def get_project_profile(base_asset: str) -> ProjectProfile | None:
    """按大写基础资产代码返回已登记的官方来源。"""
    return _PROJECT_PROFILES.get(base_asset.upper())


def build_project_reference_links(
    base_asset: str,
    *,
    include_official: bool,
    include_rootdata: bool,
) -> list[dict[str, str]]:
    """构造报告中的人工核对链接，不主动读取这些网页。"""
    profile = get_project_profile(base_asset)
    references = list(profile.references) if include_official and profile else []
    if include_rootdata:
        project_name = profile.project_name if profile else base_asset.upper()
        references.append(
            ProjectReference(
                "RootData 手动核对",
                "https://www.rootdata.com/",
                "rootdata_manual",
                f"请在浏览器中搜索 {project_name}（{base_asset.upper()}）；系统不会自动抓取 RootData 页面。",
            )
        )
    return [asdict(reference) for reference in references]
