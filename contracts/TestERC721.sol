// SPDX-License-Identifier: MIT
pragma solidity ^0.8.18;

interface IERC165 {
    function supportsInterface(bytes4 interfaceId) external view returns (bool);
}

interface IERC721 is IERC165 {
    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    function balanceOf(address owner) external view returns (uint256 balance);
    function ownerOf(uint256 tokenId) external view returns (address owner);
    function approve(address to, uint256 tokenId) external;
    function getApproved(uint256 tokenId) external view returns (address operator);
    function setApprovalForAll(address operator, bool approved) external;
    function isApprovedForAll(address owner, address operator) external view returns (bool);
    function transferFrom(address from, address to, uint256 tokenId) external;
    function safeTransferFrom(address from, address to, uint256 tokenId) external;
    function safeTransferFrom(address from, address to, uint256 tokenId, bytes calldata data) external;
}

interface IERC721Receiver {
    function onERC721Received(
        address operator,
        address from,
        uint256 tokenId,
        bytes calldata data
    ) external returns (bytes4);
}

/// @title Test ERC721
/// @notice basic ERC721-compliant for testing ERC721-sending-and-receiving contracts
/// @dev contains permissionless mint capabilities and `IERC721Receiver.onERC721Received` check for `safeTransferFrom()`
contract TestERC721 is IERC721 {
    // tokenId => owner
    mapping(uint256 => address) private _owners;
    mapping(address => uint256) private _balances;
    mapping(uint256 => address) private _tokenApprovals;
    mapping(address => mapping(address => bool)) private _operatorApprovals;

    error InvalidOwner();
    error InvalidRecipient();
    error NonERC721ReceiverImplementer();
    error NotAuthorized();
    error NotTokenOwner();
    error TokenIdAlreadyExists();
    error TokenIdDoesNotExist();

    function supportsInterface(bytes4 interfaceId) external pure override returns (bool) {
        return interfaceId == type(IERC721).interfaceId || interfaceId == type(IERC165).interfaceId;
    }

    function balanceOf(address owner) external view override returns (uint256) {
        if (owner == address(0)) revert InvalidOwner();
        return _balances[owner];
    }

    function mint(address to, uint256 tokenId) external {
        if (_owners[tokenId] != address(0)) revert TokenIdAlreadyExists();
        _balances[to] += 1;
        _owners[tokenId] = to;

        emit Transfer(address(0), to, tokenId);
    }

    function ownerOf(uint256 tokenId) external view override returns (address) {
        address owner = _owners[tokenId];
        if (owner == address(0)) revert TokenIdDoesNotExist();
        return owner;
    }

    function approve(address to, uint256 tokenId) external override {
        address owner = _owners[tokenId];
        if (msg.sender != owner && !_operatorApprovals[owner][msg.sender]) revert NotAuthorized();
        _tokenApprovals[tokenId] = to;
        emit Approval(owner, to, tokenId);
    }

    function getApproved(uint256 tokenId) external view override returns (address) {
        if (_owners[tokenId] == address(0)) revert TokenIdDoesNotExist();
        return _tokenApprovals[tokenId];
    }

    function setApprovalForAll(address operator, bool approved) external override {
        _operatorApprovals[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function isApprovedForAll(address owner, address operator) external view override returns (bool) {
        return _operatorApprovals[owner][operator];
    }

    function transferFrom(address from, address to, uint256 tokenId) public override {
        address owner = _owners[tokenId];
        if (owner != from) revert NotTokenOwner();
        if (msg.sender != owner && _tokenApprovals[tokenId] != msg.sender && !_operatorApprovals[owner][msg.sender])
            revert NotAuthorized();
        if (to == address(0)) revert InvalidRecipient();

        _beforeTokenTransfer(from, to, tokenId);

        --_balances[from];
        ++_balances[to];
        _owners[tokenId] = to;

        delete _tokenApprovals[tokenId];
        emit Transfer(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId) public override {
        safeTransferFrom(from, to, tokenId, "");
    }

    function safeTransferFrom(address from, address to, uint256 tokenId, bytes memory data) public override {
        transferFrom(from, to, tokenId);
        if (to.code.length > 0) {
            if (
                IERC721Receiver(to).onERC721Received(msg.sender, from, tokenId, data) !=
                IERC721Receiver.onERC721Received.selector
            ) revert NonERC721ReceiverImplementer();
        }
    }

    function _beforeTokenTransfer(address from, address to, uint256 tokenId) internal virtual {
        // Hook that can be overridden by inheriting contracts
    }
}
