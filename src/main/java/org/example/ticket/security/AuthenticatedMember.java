package org.example.ticket.security;

import org.example.ticket.member.domain.MemberRole;

public record AuthenticatedMember(
        Long memberId,
        String username,
        MemberRole role
) {
}
