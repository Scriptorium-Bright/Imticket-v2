package org.example.ticket.member.dto;

public record TokenResponse(
        String accessToken,
        Long memberId,
        String role
) {
}
